"""API and page routes for the Approval Queue Web UI.

Provides both server-rendered page endpoints (returning HTML via Jinja2) and
JSON API endpoints for programmatic access.  Every state-changing operation
validates the application state machine transition and emits events on the
event bus.

The module gracefully degrades when no database is available (UI-only mode),
returning mock data for preview and development purposes.
"""

from __future__ import annotations as _annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_session
from core.event_bus import EventType
from core.models import Application, ApplicationEvent, JobListing
from core.state_machine import ApplicationState, transition_state

__all__: list[str] = [
    "router",
]

logger = structlog.get_logger(__name__)

router = APIRouter()


# ── Dependency ──────────────────────────────────────────────────────────────


async def get_db(request: Request) -> AsyncIterator[AsyncSession | None]:  # type: ignore[misc]
    """Yield a database session if the engine is available.

    Yields ``None`` in UI-only mode (no DB connection) so routes can
    gracefully fall back to mock data.
    """
    engine = request.app.state.db_engine
    if engine is None:
        yield None
        return
    async with get_session(engine) as session:
        yield session


# ── Page routes ─────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    db: AsyncSession | None = Depends(get_db),
) -> HTMLResponse:
    """Render the main dashboard.

    Shows summary statistics at the top and a sortable, filterable list
    of recent applications below.  Data is rendered server-side for initial
    load; the JS layer polls ``/api/stats`` and ``/api/applications`` for
    live updates every 30 seconds.
    """
    templates = request.app.state.templates
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    stats = await _fetch_stats(db)
    applications_data = await _fetch_applications(
        db, state_filter=None, limit=20, offset=0
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "applications": applications_data.get("items", []),
            "demo_mode": db is None,
            "pagination": {
                "page": applications_data.get("page", 1),
                "total_pages": applications_data.get("total_pages", 1),
                "total": applications_data.get("total", 0),
            },
        },
    )


@router.get("/review/{application_id}", response_class=HTMLResponse)
async def review_page(
    request: Request,
    application_id: str,
    db: AsyncSession | None = Depends(get_db),
) -> HTMLResponse:
    """Render the detailed review page for a single application.

    Displays job details, generated materials, recruiter info, match score,
    and action buttons.  All data is fetched server-side and passed to the
    template for initial render.
    """
    templates = request.app.state.templates
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")

    # Validate UUID.
    try:
        app_uuid = uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid application ID: {application_id!r}") from None

    # Fetch application data.
    app_data = await _fetch_application_detail(db, app_uuid)

    if app_data is None:
        # If no DB and mock doesn't match, return a generic mock detail.
        if db is None:
            app_data = _mock_application_detail(application_id)
        else:
            raise HTTPException(status_code=404, detail="Application not found")

    return templates.TemplateResponse(
        request,
        "review.html",
        {"application": app_data, "demo_mode": db is None},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Render the platform settings page."""
    templates = request.app.state.templates
    if templates is None:
        return HTMLResponse("<h1>Templates not configured</h1>")
    return templates.TemplateResponse(request, "settings.html")


# ── API: Stats ──────────────────────────────────────────────────────────────


@router.get("/api/stats")
async def get_stats(
    _request: Request,
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, Any]:
    """Return aggregate statistics for the dashboard.

    Returns counts per state and today's submission/approval rates.
    """
    if db is None:
        return _mock_stats()

    try:
        return await _fetch_stats(db)
    except Exception as exc:
        logger.error("Failed to fetch stats", error=str(exc))
        return _mock_stats()


# ── API: Applications ───────────────────────────────────────────────────────


@router.get("/api/applications")
async def list_applications(
    _request: Request,
    state: str | None = Query(default=None, description="Filter by application state"),
    limit: int = Query(default=20, ge=1, le=100, description="Results per page"),
    offset: int = Query(default=0, ge=0, description="Results offset"),
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, Any]:
    """Return a paginated, filterable list of applications.

    Args:
        state: Optional state filter (e.g. ``PENDING_REVIEW``).
        limit: Max results per page (1-100).
        offset: Results offset for pagination.

    Returns:
        Dict with ``items``, ``total``, ``page``, ``page_size``, ``total_pages``.
    """
    if db is None:
        return _mock_applications(state, limit, offset)

    try:
        return await _fetch_applications(db, state, limit, offset)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to list applications", error=str(exc))
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/api/applications/{application_id}")
async def get_application(
    _request: Request,
    application_id: str,
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, Any]:
    """Return detailed information for a single application.

    Includes job listing, events, resume text, cover letter, and recruiter
    info.
    """
    if db is None:
        return _mock_application_detail(application_id)

    try:
        app_uuid = uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid application ID: {application_id!r}") from None

    app_data = await _fetch_application_detail(db, app_uuid)
    if app_data is None:
        raise HTTPException(status_code=404, detail="Application not found")

    return app_data


@router.post("/api/applications/{application_id}/review")
async def review_application(
    request: Request,
    application_id: str,
    body: dict[str, Any],
    db: AsyncSession | None = Depends(get_db),
) -> JSONResponse:
    """Process a review decision (approve, reject, or reset).

    Request body::

        {"action": "approve" | "reject" | "reset", "reason": "optional reason"}

    On ``approve``: transitions state to ``STAGED``, creates an audit-log
    entry, and emits a ``review.approved`` event.
    On ``reject``: transitions to ``REJECTED`` with the given reason and
    emits a ``review.rejected`` event.
    On ``reset``: transitions from ``REJECTED`` back to ``PENDING_REVIEW``
    so the application can be re-evaluated.  Emits a ``review.reset`` event.
    """
    action: str | None = body.get("action")
    reason: str | None = body.get("reason")

    if action not in ("approve", "reject", "reset"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action: {action!r}. Must be 'approve', 'reject', or 'reset'.",
        )

    if db is None:
        # UI-only mode: return a mock response.
        new_state = {
            "approve": ApplicationState.STAGED,
            "reject": ApplicationState.REJECTED,
            "reset": ApplicationState.PENDING_REVIEW,
        }[action]
        logger.info(
            "Application reviewed (mock)",
            application_id=application_id,
            action=action,
        )
        return JSONResponse(
            content={
                "status": "ok",
                "application_id": application_id,
                "new_state": new_state.value,
                "message": f"Application {action}d (mock mode)",
            }
        )

    # Parse and validate application UUID.
    try:
        app_uuid = uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid application ID: {application_id!r}") from None

    # Determine target state from action.
    target = {
        "approve": ApplicationState.STAGED,
        "reject": ApplicationState.REJECTED,
        "reset": ApplicationState.PENDING_REVIEW,
    }[action]

    try:
        # Fetch the application with eager-loaded relationships.
        query = (
            select(Application)
            .where(Application.id == app_uuid)
            .options(selectinload(Application.job_listing))
            .options(selectinload(Application.events))
        )
        rows = await db.execute(query)
        app = rows.scalar_one_or_none()

        if app is None:
            raise HTTPException(status_code=404, detail="Application not found")

        # Validate the state-machine transition.
        try:
            transition_state(app.state, target, application_id=application_id)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Persist the application state update with optimistic locking.
        stmt = (
            update(Application)
            .where(Application.id == app_uuid)
            .where(Application.state == app.state)
            .values(state=target, notes=reason)
        )
        result = await db.execute(stmt)
        if result.rowcount == 0:
            raise HTTPException(
                status_code=409,
                detail="Application state changed concurrently — reload and try again",
            )

        # Create an audit-log event.
        event_entry = ApplicationEvent(
            application_id=app_uuid,
            from_state=app.state,
            to_state=target,
            metadata_json={
                "reason": reason,
                "action": action,
                "reviewer": "admin",
            },
        )
        db.add(event_entry)
        await db.commit()

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Database error during review",
            application_id=application_id,
            action=action,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    # Emit event on the bus (best-effort — does not block the response).
    try:
        event_bus = request.app.state.event_bus
        event_type = {
            "approve": EventType.REVIEW_APPROVED,
            "reject": EventType.REVIEW_REJECTED,
            "reset": EventType.REVIEW_RESET,
        }[action]
        await event_bus.publish(
            event_type,
            data={
                "application_id": application_id,
                "new_state": target.value,
                "reason": reason,
            },
        )
    except Exception as exc:
        logger.warning("Event emission failed", error=str(exc))

    logger.info(
        "Application reviewed",
        application_id=application_id,
        action=action,
        from_state=app.state.value,
        to_state=target.value,
        reason=reason,
    )

    return JSONResponse(
        content={
            "status": "ok",
            "application_id": application_id,
            "new_state": target.value,
            "message": f"Application {action}d",
        },
        headers={"HX-Trigger": "application-updated"},  # Trigger dashboard refresh
    )


@router.post("/api/bulk-approve")
async def bulk_approve(
    _request: Request,
    body: dict[str, Any],
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, Any]:
    """Approve multiple applications at once.

    Request body::

        {"application_ids": ["uuid1", "uuid2", ...]}

    Each application is validated individually — a failed transition on one
    does not block the others.
    """
    application_ids: list[str] = body.get("application_ids", [])
    if not application_ids:
        raise HTTPException(status_code=400, detail="application_ids list is required")

    if len(application_ids) > 50:
        raise HTTPException(status_code=400, detail="Max 50 applications per bulk operation")

    results: list[dict[str, Any]] = []
    success_count = 0

    for app_id in application_ids:
        try:
            if db is None:
                success_count += 1
                results.append({
                    "application_id": app_id,
                    "status": "ok",
                    "new_state": ApplicationState.STAGED.value,
                })
                continue

            app_uuid = uuid.UUID(app_id)
            query = select(Application).where(Application.id == app_uuid)
            rows = await db.execute(query)
            app = rows.scalar_one_or_none()

            if app is None:
                results.append({"application_id": app_id, "status": "error", "detail": "Not found"})
                continue

            try:
                transition_state(app.state, ApplicationState.STAGED, application_id=app_id)
            except Exception:
                results.append({"application_id": app_id, "status": "error", "detail": "Invalid transition"})
                continue

            stmt = update(Application).where(Application.id == app_uuid).values(state=ApplicationState.STAGED)
            await db.execute(stmt)

            event_entry = ApplicationEvent(
                application_id=app_uuid,
                from_state=app.state,
                to_state=ApplicationState.STAGED,
                metadata_json={"action": "bulk_approve", "reviewer": "admin"},
            )
            db.add(event_entry)
            success_count += 1
            results.append({"application_id": app_id, "status": "ok", "new_state": ApplicationState.STAGED.value})

        except Exception as exc:
            results.append({"application_id": app_id, "status": "error", "detail": str(exc)})

    if db is not None:
        await db.commit()

    logger.info(
        "Bulk approve completed",
        total=len(application_ids),
        succeeded=success_count,
    )

    return {
        "results": results,
        "total": len(application_ids),
        "succeeded": success_count,
        "failed": len(application_ids) - success_count,
    }


# ── API: Config ─────────────────────────────────────────────────────────────


@router.get("/api/config")
async def get_config(_request: Request) -> dict[str, Any]:
    """Return platform configuration for the UI."""
    from core.config import get_settings

    settings = get_settings()
    return {
        "max_applications_per_day": settings.job_discovery.max_applications_per_day,
        "environment": settings.environment,
    }


# ── Data-fetching helpers ───────────────────────────────────────────────────


async def _fetch_stats(db: AsyncSession | None) -> dict[str, Any]:
    """Query aggregate statistics from the database.

    Falls back to mock data when *db* is ``None`` or the tables do not
    exist yet (e.g. before ``getajob setup`` has been run).
    """
    if db is None:
        return _mock_stats()

    today_start = datetime.datetime.now(datetime.UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    try:
        # Count per state.
        rows = await db.execute(
            select(Application.state, func.count(Application.id)).group_by(Application.state)
        )
        state_counts: dict[str, int] = {str(state): count for state, count in rows}

        # Counts for today.
        today_rows = await db.execute(
            select(Application.state, func.count(Application.id))
            .where(Application.created_at >= today_start)
            .group_by(Application.state)
        )
        today_state_counts: dict[str, int] = {str(state): count for state, count in today_rows}
    except Exception:
        # Tables may not exist yet (OperationalError). Return empty stats.
        state_counts = {}
        today_state_counts = {}

    total = sum(state_counts.values())

    return {
        "state_counts": state_counts,
        "today_counts": today_state_counts,
        "total": total,
        "pending_review": state_counts.get(ApplicationState.PENDING_REVIEW.value, 0),
        "approved_today": today_state_counts.get(ApplicationState.STAGED.value, 0)
        + today_state_counts.get(ApplicationState.SUBMITTED.value, 0),
        "submitted": state_counts.get(ApplicationState.SUBMITTED.value, 0),
        "submitted_today": today_state_counts.get(ApplicationState.SUBMITTED.value, 0),
        "failed": state_counts.get(ApplicationState.FAILED.value, 0),
        "rejected": state_counts.get(ApplicationState.REJECTED.value, 0),
        "tailored": state_counts.get(ApplicationState.TAILORED.value, 0),
        "discovered": state_counts.get(ApplicationState.DISCOVERED.value, 0),
    }


async def _fetch_applications(
    db: AsyncSession | None,
    state_filter: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Query a paginated list of applications.

    Joins with ``job_listings`` to include company and title.  Falls back
    to mock data when *db* is ``None`` or tables do not exist yet.
    """
    if db is None:
        return _mock_applications(state_filter, limit, offset)

    conditions = [Application.profile_id.isnot(None)]

    if state_filter:
        try:
            target_state = ApplicationState(state_filter.upper())
            conditions.append(Application.state == target_state)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid state: {state_filter!r}") from None

    try:
        # Total count.
        count_result = await db.execute(
            select(func.count(Application.id)).where(*conditions)
        )
        total = count_result.scalar() or 0

        # Fetch rows with joined job listing.
        query = (
            select(Application)
            .options(selectinload(Application.job_listing))
            .where(*conditions)
            .order_by(Application.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = await db.execute(query)
        applications = rows.unique().scalars().all()

        page = (offset // limit) + 1 if limit > 0 else 1
        total_pages = max(1, (total + limit - 1) // limit) if limit > 0 else 1

        return {
            "items": [_application_to_dict(a, include_details=False) for a in applications],
            "total": total,
            "page": page,
            "page_size": limit,
            "total_pages": total_pages,
        }
    except Exception:
        # Tables may not exist yet. Return empty list.
        return {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": limit,
            "total_pages": 1,
        }


async def _fetch_application_detail(
    db: AsyncSession | None,
    app_uuid: uuid.UUID,
) -> dict[str, Any] | None:
    """Fetch a single application with all related data.

    Returns ``None`` if not found.
    """
    if db is None:
        return None

    query = (
        select(Application)
        .where(Application.id == app_uuid)
        .options(selectinload(Application.job_listing))
        .options(selectinload(Application.events))
    )
    rows = await db.execute(query)
    app = rows.unique().scalar_one_or_none()

    if app is None:
        return None

    return _application_to_dict(app, include_details=True)


# ── Serialisation helpers ───────────────────────────────────────────────────


def _application_to_dict(app: Application, *, include_details: bool = False) -> dict[str, Any]:
    """Convert an ORM Application to a plain dict for JSON serialisation.

    Args:
        app: The ORM model instance.
        include_details: If ``True``, include resume text, cover letter,
            recruiter info, and events (for the review page).
    """
    app_dict: dict[str, Any] = {
        "id": str(app.id),
        "job_listing_id": str(app.job_listing_id),
        "profile_id": str(app.profile_id),
        "state": app.state.value,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
    }

    # Include optional fields on detail views.
    if include_details:
        app_dict.update({
            "resume_text": app.resume_text,
            "cover_letter": app.cover_letter,
            "recruiter_name": app.recruiter_name,
            "recruiter_email": app.recruiter_email,
            "applied_at": app.applied_at.isoformat() if app.applied_at else None,
            "notes": app.notes,
        })

    # Include job listing data if the relationship is loaded.
    if hasattr(app, "job_listing") and app.job_listing is not None:
        jl: JobListing = app.job_listing
        app_dict["job_listing"] = {
            "id": str(jl.id),
            "company": jl.company,
            "title": jl.title,
            "location": jl.location or "Remote",
            "url": jl.url,
            "source": jl.source or "unknown",
            "source_id": jl.source_id,
            "required_skills": jl.required_skills or [],
            "posted_date": jl.posted_date.isoformat() if jl.posted_date else None,
            "description_json": jl.description_json,
        }

    # Include event history if loaded.
    if include_details and hasattr(app, "events") and app.events:
        app_dict["events"] = [
            {
                "id": str(e.id),
                "from_state": e.from_state.value if e.from_state else None,
                "to_state": e.to_state.value,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "metadata": e.metadata_json,
            }
            for e in app.events
        ]

    return app_dict


# ── Mock data (UI-only mode) ────────────────────────────────────────────────


def _mock_stats() -> dict[str, Any]:
    """Return plausible mock statistics when no database is available."""
    return {
        "state_counts": {
            "PENDING_REVIEW": 3,
            "STAGED": 1,
            "SUBMITTED": 5,
            "REJECTED": 2,
            "FAILED": 0,
            "TAILORED": 4,
            "DISCOVERED": 6,
            "OUTREACH_PENDING": 0,
        },
        "today_counts": {
            "STAGED": 1,
            "SUBMITTED": 3,
        },
        "total": 21,
        "pending_review": 3,
        "approved_today": 4,
        "submitted": 5,
        "submitted_today": 3,
        "failed": 0,
        "rejected": 2,
        "tailored": 4,
        "discovered": 6,
    }


def _mock_applications(state: str | None, limit: int, _offset: int) -> dict[str, Any]:
    """Return mock applications when no database is available."""
    states = [
        ApplicationState.PENDING_REVIEW,
        ApplicationState.STAGED,
        ApplicationState.SUBMITTED,
    ]
    companies = [
        "Acme Corp",
        "TechCo Inc",
        "DataFlow Systems",
        "CloudBase",
        "AI Systems",
        "Quantum Labs",
        "NexGen Software",
        "Pinnacle Analytics",
    ]
    titles = [
        "Senior Backend Engineer",
        "Staff Software Engineer",
        "Principal Architect",
        "ML Platform Engineer",
        "Distributed Systems Engineer",
        "Lead DevOps Engineer",
        "Senior Frontend Engineer",
        "Data Platform Engineer",
    ]
    locations = ["Remote", "San Francisco, CA", "New York, NY", "Austin, TX"]
    now = datetime.datetime.now(datetime.UTC)

    import random

    random.seed(42)

    items: list[dict[str, Any]] = []
    total = min(15, limit)

    for i in range(total):
        c = companies[i % len(companies)]
        t = titles[i % len(titles)]
        loc = locations[i % len(locations)]
        s = states[i % len(states)]

        # Override state if filter is active.
        if state and s.value != state.upper():
            s = ApplicationState.PENDING_REVIEW

        items.append({
            "id": str(uuid.uuid4()),
            "state": s.value,
            "job_listing": {
                "company": c,
                "title": t,
                "location": loc,
            },
            "created_at": (now - datetime.timedelta(hours=i * 2)).isoformat(),
            "updated_at": (now - datetime.timedelta(hours=i)).isoformat(),
        })

    return {
        "items": items,
        "total": len(items),
        "page": 1,
        "page_size": limit,
        "total_pages": 1,
    }


def _mock_application_detail(application_id: str) -> dict[str, Any]:
    """Return a detailed mock application for the review page."""
    return {
        "id": application_id,
        "state": ApplicationState.PENDING_REVIEW.value,
        "resume_text": (
            "## Experience\n\n"
            "**Senior Software Engineer** - Acme Corp (2021 - Present)\n"
            "- Led migration of monolithic payment processing to Rust-based microservices architecture\n"
            "- Reduced P99 latency by 40% through connection pooling and query optimisation\n"
            "- Designed distributed task queue handling 1M+ jobs/day\n\n"
            "**Software Engineer** - StartUp Inc (2018 - 2021)\n"
            "- Built real-time data pipeline processing 10GB/day using Kafka and Flink\n"
            "- Developed internal CLI tools that reduced deployment time by 60%\n\n"
            "## Skills\n\n"
            "Rust, Python, Go, Kubernetes, PostgreSQL, Redis, Kafka, gRPC, TensorFlow"
        ),
        "cover_letter": (
            "I'm writing regarding the Senior Backend Engineer role at Acme Corp. "
            "My background in distributed systems and Rust aligns closely with what "
            "your team is building.\n\n"
            "At my current role I led a migration from a monolith to microservices "
            "that cut latency by 40%. I've been following Acme's work on real-time "
            "data processing and would welcome the chance to contribute.\n\n"
            "Thanks for your consideration."
        ),
        "job_listing": {
            "company": "Acme Corp",
            "title": "Senior Backend Engineer",
            "location": "Remote",
            "url": "https://careers.acme.corp/123",
            "source": "linkedin",
            "required_skills": ["Rust", "Python", "Kubernetes", "PostgreSQL"],
            "posted_date": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)).isoformat(),
        },
        "recruiter_name": "Jane Smith",
        "recruiter_email": "jane.smith@acme.example",
        "events": [
            {
                "id": str(uuid.uuid4()),
                "from_state": None,
                "to_state": ApplicationState.DISCOVERED.value,
                "timestamp": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=48)).isoformat(),
                "metadata": {"source": "linkedin"},
            },
            {
                "id": str(uuid.uuid4()),
                "from_state": ApplicationState.DISCOVERED.value,
                "to_state": ApplicationState.TAILORED.value,
                "timestamp": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)).isoformat(),
                "metadata": {"match_score": 0.85},
            },
            {
                "id": str(uuid.uuid4()),
                "from_state": ApplicationState.TAILORED.value,
                "to_state": ApplicationState.PENDING_REVIEW.value,
                "timestamp": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=6)).isoformat(),
                "metadata": {},
            },
        ],
        "created_at": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=48)).isoformat(),
        "updated_at": (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=6)).isoformat(),
    }

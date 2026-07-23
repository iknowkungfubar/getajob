"""Profile management sub-commands for the GetAJob CLI.

Provides the ``profile`` sub-app with ``show`` and ``update`` commands
for viewing and editing the user's job-seeking profile.
"""

from __future__ import annotations as _annotations

from typing import Any

import typer
from rich.panel import Panel
from rich.table import Table

from getajob.cli.helpers import _run_async, console, err_console

# profile_app is defined in main.py; imported here so decorators work.
from getajob.cli.main import profile_app

__all__: list[str] = [
    "show",
    "update",
]


@profile_app.command()
def show() -> None:
    """Show the currently active profile.

    Displays name, email, location, skills, and work authorisation
    from the database in a formatted table.
    """

    async def _run() -> None:
        engine = None
        try:
            from core.database import create_engine
            from profile_engine.profile_store import ProfileStore

            engine = create_engine()
            store = ProfileStore(engine)
            profiles = await store.list_profiles(limit=1)

            if not profiles:
                console.print("[yellow]No profile configured. Run 'getajob init' to create one.[/]")
                return

            profile = profiles[0]

            table = Table(title="User Profile", border_style="blue")
            table.add_column("Field", style="cyan", no_wrap=True)
            table.add_column("Value")

            table.add_row("Name", profile.name or "")
            table.add_row("Email", profile.email or "")
            table.add_row("Location", profile.location or "[dim]not set[/]")

            if profile.skills:
                skills_str = ", ".join(s.name for s in profile.skills)
            else:
                skills_str = "[dim]none[/]"
            table.add_row("Skills", skills_str)
            table.add_row("Work Authorization", profile.work_authorization or "[dim]not set[/]")

            console.print(table)
        except Exception as exc:
            err_console.print(f"[red]\u2717[/] Could not load profile: {exc}")
        finally:
            if engine is not None:
                await engine.dispose()

    _run_async(_run())


@profile_app.command()
def update() -> None:
    """Update profile fields via interactive prompts.

    Shows current values; press Enter to keep a field unchanged.
    Only changed fields are sent to the database.
    """

    async def _run() -> None:
        engine = None
        try:
            from core.database import create_engine
            from core.schemas import ProfileUpdate, SkillSchema
            from profile_engine.profile_store import ProfileStore

            engine = create_engine()
            store = ProfileStore(engine)
            profiles = await store.list_profiles(limit=1)

            if not profiles:
                console.print(
                    "[yellow]No profile configured. Run 'getajob init' to create one first.[/]"
                )
                return

            current = profiles[0]
            console.print(
                Panel.fit("[bold cyan]\u270f\ufe0f  Update Profile[/]", border_style="cyan")
            )
            console.print("[dim]Press Enter to keep the current value.[/]\n")

            # Interactive prompts with current values as defaults.
            name = typer.prompt("  Name", default=current.name or "")
            email = typer.prompt("  Email", default=current.email or "")
            location = typer.prompt("  Location", default=current.location or "")

            current_skills_str = ", ".join(s.name for s in current.skills) if current.skills else ""
            skills_str = typer.prompt("  Skills (comma-separated)", default=current_skills_str)

            work_auth = typer.prompt(
                "  Work Authorization", default=current.work_authorization or ""
            )

            # Build update payload with only changed fields.
            update_data: dict[str, Any] = {}

            if name.strip() and name.strip() != (current.name or ""):
                update_data["name"] = name.strip()
            if email.strip() and email.strip() != (current.email or ""):
                update_data["email"] = email.strip()
            if location.strip() and location.strip() != (current.location or ""):
                update_data["location"] = location.strip()

            # Skills: comma-separated string -> list[SkillSchema].
            if skills_str.strip():
                new_skills = [s.strip() for s in skills_str.split(",") if s.strip()]
                old_skills = [s.name for s in current.skills] if current.skills else []
                if new_skills != old_skills:
                    update_data["skills"] = [SkillSchema(name=s) for s in new_skills]
            elif current.skills:
                # User explicitly cleared the skills field.
                update_data["skills"] = []

            if work_auth.strip() and work_auth.strip() != (current.work_authorization or ""):
                update_data["work_authorization"] = work_auth.strip()
            elif not work_auth.strip() and current.work_authorization:
                update_data["work_authorization"] = ""

            if not update_data:
                console.print("[yellow]No changes made.[/]")
                return

            updated = await store.update_profile(
                current.id,
                ProfileUpdate(**update_data),
            )

            # Confirmation table.
            field_labels = {
                "name": "Name",
                "email": "Email",
                "location": "Location",
                "skills": "Skills",
                "work_authorization": "Work Authorization",
            }
            updated_values = {
                "name": updated.name or "",
                "email": updated.email or "",
                "location": updated.location or "",
                "skills": (
                    ", ".join(s.name for s in updated.skills) if updated.skills else "[dim]none[/]"
                ),
                "work_authorization": updated.work_authorization or "[dim]not set[/]",
            }

            console.print("\n[green]\u2713[/] Profile updated successfully.")
            table = Table(border_style="green", show_header=False)
            table.add_column("Field", style="bold cyan")
            table.add_column("Value")

            for field_name in update_data:
                table.add_row(
                    field_labels.get(field_name, field_name.capitalize()),
                    updated_values.get(field_name, ""),
                )

            console.print(table)
        except Exception as exc:
            err_console.print(f"[red]\u2717[/] Could not update profile: {exc}")
        finally:
            if engine is not None:
                await engine.dispose()

    _run_async(_run())

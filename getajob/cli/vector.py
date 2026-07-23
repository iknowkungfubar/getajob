"""Vector (search vector) sub-commands for the GetAJob CLI.

Provides the ``vector`` sub-app with ``list`` and ``add`` commands
for managing job-search vectors in config/settings.yaml.
"""

from __future__ import annotations as _annotations

from pathlib import Path
from typing import Any

import typer
import yaml
from rich.panel import Panel
from rich.table import Table

from getajob.cli.helpers import console
from getajob.cli.main import vector_app

__all__: list[str] = [
    "vector_add",
    "vector_list",
]


@vector_app.command(name="list")
def vector_list() -> None:
    """List all configured search vectors from config/settings.yaml.

    Displays a Rich-formatted table with name, keywords, locations, and
    remote preference for each configured search vector.
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    yaml_path = project_root / "config" / "settings.yaml"

    if not yaml_path.exists():
        console.print(
            "[yellow]No search vectors configured. Run 'getajob init' to set up defaults.[/]"
        )
        raise typer.Exit()

    with yaml_path.open("r") as f:
        data = yaml.safe_load(f) or {}

    vectors = data.get("search_vectors", [])
    if not vectors:
        console.print(
            "[yellow]No search vectors configured. Run 'getajob init' to set up defaults.[/]"
        )
        raise typer.Exit()

    table = Table(title="Search Vectors", border_style="blue")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Keywords", style="green")
    table.add_column("Locations", style="yellow")
    table.add_column("Remote", justify="center")

    for vec in vectors:
        # Use explicit name or fall back to first two roles.
        name = vec.get("name") or ", ".join(vec.get("roles", ["(unnamed)"])[:2])
        kw_list = vec.get("keywords", [])
        keywords = ", ".join(kw_list[:5])
        if len(kw_list) > 5:
            keywords += "\u2026"

        loc_list = vec.get("locations", [])
        locations = ", ".join(loc_list) if loc_list else "[dim]any[/]"

        is_remote = any(loc.strip().lower() == "remote" for loc in loc_list)
        remote_str = "[green]\u2713[/]" if is_remote else "[red]\u2717[/]"

        table.add_row(name, keywords, locations, remote_str)

    console.print(table)


@vector_app.command(name="add")
def vector_add() -> None:
    """Add a new search vector with interactive prompts.

    Walks through the required fields (name, keywords) and optional fields
    (location, remote preference), then appends the new vector to the
    search_vectors list in config/settings.yaml.
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    yaml_path = project_root / "config" / "settings.yaml"

    console.print(Panel.fit("[bold green]+  Add Search Vector[/]", border_style="green"))

    # Interactive prompts.
    vec_name = typer.prompt("  Vector name")
    console.print("  [dim]e.g. 'Backend Engineer' or 'Frontend Developer'[/]")

    keywords_str = typer.prompt("  Keywords (comma-separated)")
    console.print("  [dim]e.g. python, go, kubernetes, aws[/]")

    location = typer.prompt("  Location (optional, press Enter to skip)", default="")
    remote = typer.confirm("  Remote only?", default=True)

    # Build vector.
    keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]

    locations: list[str] = []
    if location.strip():
        locations.append(location.strip())
    if remote and "remote" not in [loc.strip().lower() for loc in locations]:
        locations.insert(0, "remote")

    new_vector: dict[str, Any] = {
        "name": vec_name,
        "keywords": keywords,
        "locations": locations,
    }

    # Read / update YAML.
    if yaml_path.exists():
        with yaml_path.open("r") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    else:
        data = {}

    vectors = data.setdefault("search_vectors", [])
    vectors.append(new_vector)

    with yaml_path.open("w") as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    # Confirmation.
    console.print()
    table = Table(border_style="green", show_header=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Name", vec_name)
    table.add_row("Keywords", ", ".join(keywords))
    table.add_row(
        "Locations",
        ", ".join(locations) if locations else "[dim]any[/]",
    )
    table.add_row("Remote", "[green]Yes[/]" if remote else "[red]No[/]")
    console.print(table)
    console.print(f"\n[green]\u2713[/] Vector '[bold]{vec_name}[/]' added successfully.")

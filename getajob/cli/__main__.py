"""Allow ``python -m getajob.cli`` to invoke the CLI app."""
from getajob.cli.main import app

if __name__ == "__main__":
    app()

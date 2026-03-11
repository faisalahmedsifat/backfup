

import typer


def ask_if_missing(value, message, hide_input=False):
    if value is not None:
        return value
    return typer.prompt(message, hide_input=hide_input)


def choose_option(message, options: dict, default=None):
    """
    options: dict where
        key   = internal identifier
        value = label shown to user
    """

    typer.echo(message)

    keys = list(options.keys())

    for i, key in enumerate(keys, start=1):
        typer.echo(f"{i}) {options[key]}")

    choice = typer.prompt("Choice", default=default)

    try:
        index = int(choice) - 1
        return keys[index]
    except Exception:
        typer.echo("Invalid choice.")
        raise typer.Exit(1)
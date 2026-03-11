
import typer


def ask_if_missing(value, message):
    if value is not None:
        return value
    return typer.prompt(message)

def ask_if_missing_creds(value, message):
    if value is not None:
        return value
    return typer.prompt(message, hide_input=True)
    
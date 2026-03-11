import typer
from typing import Optional
from utils.prompts import ask_if_missing, choose_option
from loguru import logger


def init_command(
    endpoint: Optional[str] = typer.Option(
        None,
        help="S3 endpoint URL"
    ),
    bucket: Optional[str] = typer.Option(
        None,
        help="S3 bucket name"
    ),
    region: Optional[str] = typer.Option(
        None,
        help="S3 region"
    ),
    access_key: Optional[str] = typer.Option(
        None,
        help="Access key"
    ),
    secret_key: Optional[str] = typer.Option(
        None,
        help="Secret key"
    ),
    access_key_env: Optional[str] = typer.Option(
        None,
        help="Environment variable containing access key"
    ),
    secret_key_env: Optional[str] = typer.Option(
        None,
        help="Environment variable containing secret key"
    ),
    force: bool = typer.Option(
        False,
        help="Overwrite existing configuration"
    )
):
    # FIX 1: Guard clause — reject ambiguous credential mix upfront
    if (access_key or secret_key) and (access_key_env or secret_key_env):
        typer.echo(
            "Cannot provide both direct credentials and environment variable names. "
            "Please choose one method, or omit both and you will be prompted during execution."
        )
        raise typer.Exit(1)

    # FIX 2: Always prompt for missing connection params, regardless of credential method
    endpoint = ask_if_missing(endpoint, "Enter S3 endpoint URL")
    bucket = ask_if_missing(bucket, "Enter S3 bucket name")
    region = ask_if_missing(region, "Enter S3 region")

    # FIX 3: Only ask HOW to provide credentials if neither method was given on the CLI
    credentials_provided = (access_key and secret_key) or (access_key_env and secret_key_env)

    if not credentials_provided:
        option = choose_option(
            "Choose how to provide credentials:",
            {"env": "Environment variables (Recommended)", "direct": "Direct input"},
            default="1"
        )
        logger.debug(f"User chose credential option: {option}")
    elif access_key_env or secret_key_env:
        option = "env"
    else:
        option = "direct"

    # FIX 4: Prompt only for the missing half of whichever method was chosen/detected
    if option == "env":
        access_key_env = ask_if_missing(access_key_env, "Enter environment variable name for access key")
        secret_key_env = ask_if_missing(secret_key_env, "Enter environment variable name for secret key")
    else:
        access_key = ask_if_missing(access_key, "Enter access key", hide_input=True)
        secret_key = ask_if_missing(secret_key, "Enter secret key", hide_input=True)

    logger.debug(
        f"Resolved config — endpoint={endpoint}, bucket={bucket}, region={region}, "
        f"credential_method={'env' if option == 'env' else 'direct'}"
    )

    # FIX 5: Build the config dict that will actually be persisted
    config = {
        "endpoint": endpoint,
        "bucket": bucket,
        "region": region,
    }

    if option == "env":
        config["access_key_env"] = access_key_env
        config["secret_key_env"] = secret_key_env
    else:
        config["access_key"] = access_key
        config["secret_key"] = secret_key

    # TODO: persist `config` to disk (e.g. ~/.config/yourapp/config.json)
    #       and respect the `force` flag when a config file already exists.
    typer.echo("Configuration collected successfully.")
    return config
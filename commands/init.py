import typer
from typing import Optional
from utils.prompts import ask_if_missing, choose_option
from utils.credentials import encode_env_ref
from config.store import ConfigStore
from loguru import logger


def init_command(
    endpoint: Optional[str] = typer.Option(None, help="S3 endpoint URL"),
    bucket: Optional[str] = typer.Option(None, help="S3 bucket name"),
    region: Optional[str] = typer.Option(None, help="S3 region"),
    access_key: Optional[str] = typer.Option(None, help="Access key"),
    secret_key: Optional[str] = typer.Option(None, help="Secret key"),
    access_key_env: Optional[str] = typer.Option(None, help="Environment variable containing access key"),
    secret_key_env: Optional[str] = typer.Option(None, help="Environment variable containing secret key"),
    force: bool = typer.Option(False, help="Overwrite existing configuration"),
):
    # Guard: reject ambiguous credential mix upfront
    if (access_key or secret_key) and (access_key_env or secret_key_env):
        typer.echo(
            "Cannot provide both direct credentials and environment variable names. "
            "Please choose one method, or omit both and you will be prompted during execution."
        )
        raise typer.Exit(1)

    store = ConfigStore()
    config = store.load() if store.exists() else {}

    if "storage" in config and not force:
        typer.echo("Storage is already configured. Use --force to overwrite.")
        raise typer.Exit(1)

    # Collect connection params
    endpoint = ask_if_missing(endpoint, "Enter S3 endpoint URL")
    bucket = ask_if_missing(bucket, "Enter S3 bucket name")
    region = ask_if_missing(region, "Enter S3 region")

    # Determine credential method
    credentials_provided = (access_key and secret_key) or (access_key_env and secret_key_env)

    if not credentials_provided:
        option = choose_option(
            "Choose how to provide credentials:",
            {"env": "Environment variables (Recommended)", "direct": "Direct input"},
            default="1",
        )
        logger.debug(f"User chose credential option: {option}")
    elif access_key_env or secret_key_env:
        option = "env"
    else:
        option = "direct"

    # Collect and encode credentials
    if option == "env":
        access_key_env = ask_if_missing(access_key_env, "Enter environment variable name for access key")
        secret_key_env = ask_if_missing(secret_key_env, "Enter environment variable name for secret key")
        stored_access_key = encode_env_ref(access_key_env)
        stored_secret_key = encode_env_ref(secret_key_env)
    else:
        access_key = ask_if_missing(access_key, "Enter access key", hide_input=True)
        secret_key = ask_if_missing(secret_key, "Enter secret key", hide_input=True)
        stored_access_key = access_key
        stored_secret_key = secret_key

    config["storage"] = {
        "endpoint": endpoint,
        "bucket": bucket,
        "region": region,
        "access_key": stored_access_key,
        "secret_key": stored_secret_key,
    }

    logger.debug(
        f"Saving storage — endpoint={endpoint}, bucket={bucket}, "
        f"region={region}, credential_method={option}"
    )

    store.save(config)
    typer.echo("Storage configured successfully.")
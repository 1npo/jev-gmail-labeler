"""Google OAuth: the interactive consent flow and token loading."""

from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from jev_gmail_labeler import files
from jev_gmail_labeler.errors import AuthError, ConfigError
from jev_gmail_labeler.logging_setup import register_secret

SCOPES = (
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/pubsub',
)


def save_credentials(creds: Credentials, token_file: Path) -> None:
    """Write the token file readable only by the current user."""
    files.write_text_atomic(token_file, creds.to_json(), mode=0o600)


def _register_secrets(creds: Credentials) -> None:
    register_secret(creds.token)
    register_secret(creds.refresh_token)


def run_consent_flow(
    credentials_file: Path,
    token_file: Path,
    *,
    port: int = 0,
    open_browser: bool = True,
) -> Credentials:
    """Run the browser consent flow and save the resulting token."""
    try:
        client_config = files.read_json(credentials_file)
    except FileNotFoundError:
        raise ConfigError(
            f'OAuth client file not found: {credentials_file}. Create a Desktop app '
            'OAuth client in Google Cloud Console and save its JSON there.'
        ) from None
    except ValueError as e:
        raise ConfigError(str(e)) from e
    flow = InstalledAppFlow.from_client_config(client_config, list(SCOPES))
    creds = flow.run_local_server(port=port, open_browser=open_browser)
    save_credentials(creds, token_file)
    _register_secrets(creds)
    return creds


def load_credentials(token_file: Path) -> Credentials:
    """Load the saved token, refreshing it if needed."""
    try:
        info = files.read_json(token_file)
    except FileNotFoundError:
        raise AuthError(f'No token at {token_file}') from None
    except ValueError as e:
        raise AuthError(str(e)) from e
    try:
        # No scopes argument: the scopes recorded in the token are what get checked.
        creds = Credentials.from_authorized_user_info(info)
    except ValueError as e:
        raise AuthError(f'Token file is invalid: {e}') from e
    if not creds.has_scopes(SCOPES):
        raise AuthError('Token is missing required scopes')
    if not creds.valid:
        if not creds.refresh_token:
            raise AuthError('Token is expired and has no refresh token')
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(f'Token refresh failed: {e}') from e
        except TransportError as e:
            raise AuthError(f'Token refresh failed: {e}', transient=True) from e
        save_credentials(creds, token_file)
    _register_secrets(creds)
    return creds

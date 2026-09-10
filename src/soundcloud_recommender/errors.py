"""Errors with messages safe to surface through the MCP boundary."""


class ListeningCompanionError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(ListeningCompanionError):
    code = "configuration_required"


class ValidationError(ListeningCompanionError):
    code = "invalid_input"


class AuthenticationRequired(ListeningCompanionError):
    code = "authentication_required"


class OAuthError(ListeningCompanionError):
    code = "oauth_error"


class AllowlistError(ListeningCompanionError):
    code = "endpoint_not_allowed"


class ProviderError(ListeningCompanionError):
    code = "provider_error"


class CacheMiss(ListeningCompanionError):
    code = "expired_or_unknown_result"


class ConsentRequired(ListeningCompanionError):
    code = "explicit_consent_required"


class UnsupportedOperation(ListeningCompanionError):
    code = "unsupported_operation"


# Compatibility names from the initial scaffold; they are aliases, not a
# second error surface.
AuthenticationError = AuthenticationRequired
NotConnectedError = AuthenticationRequired
OAuthStateError = OAuthError
TransportError = ProviderError

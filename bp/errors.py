"""Exceptions the engine raises deliberately, so callers can tell failures apart."""


class BluePencilError(Exception):
    """Base class for every error the engine raises on purpose."""


class ProfileError(BluePencilError):
    """The series profile is missing, malformed, or internally inconsistent."""


class PolicyError(BluePencilError):
    """The run policy is missing or malformed."""


class UncitedClaim(BluePencilError):
    """A graph record was written without a citation.

    Enforced at write time: the graph is only trustworthy if every claim in it
    points at the text that supports it.
    """


class UnknownDistance(BluePencilError):
    """The space model has no distance for a pair of places."""


class CalendarError(BluePencilError):
    """A date string could not be parsed in the profile's calendar."""


class NoCredentials(BluePencilError):
    """A model-backed stage was invoked with no API key configured."""


class Refused(BluePencilError):
    """The model returned a ``refusal`` stop reason and no fallback succeeded."""


class NoStructuredOutput(BluePencilError):
    """A structured call ended with no ``tool_use`` block, even forced.

    Distinct from :class:`Refused`: this is not a safety refusal, it is a model
    that took ``tool_choice: auto`` at its word and answered in prose instead —
    typically because the honest answer to the prompt is a clarifying question.
    """

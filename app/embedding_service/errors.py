class IngestError(Exception):
    """A problem the user can fix (bad folder, service down, ...); shown verbatim in the UI."""


class IngestCancelled(Exception):
    """Raised inside the pipeline when the user presses Cancel."""

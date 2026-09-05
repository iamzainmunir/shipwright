"""3rd-party connectors (doc 07). Phase 3 ships the GitHub connector."""

from .github import GitHubConnector, PullRequestResult, PushRejected

__all__ = ["GitHubConnector", "PullRequestResult", "PushRejected"]

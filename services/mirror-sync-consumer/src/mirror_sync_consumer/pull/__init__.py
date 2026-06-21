"""DB-pull mode — the v1.0 launch path (decisions.md D35).

``reader`` reads Customer Master under the platform read context; ``runner`` is the
run-to-completion entrypoint. The Pub/Sub consumer mode is deferred and not built here.
"""

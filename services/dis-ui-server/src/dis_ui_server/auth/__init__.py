"""The auth seam: token verification and request scoping (contract §2.1/§2.2).

``verifier.py`` is the single swap point for 13b's Customer Master JWKS
verifier; ``scope.py`` holds the FastAPI dependencies that are the SOLE source
of ``tenant_id``; ``identity.py`` holds the ``Identity`` value they yield.
"""

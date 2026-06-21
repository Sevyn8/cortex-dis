"""The DIS write sink for identity_mirror.

Writes through ``dis-rls`` ``rls_session`` so the target-safety guard
(``current_database()=='ithina_dis_db'`` + NOBYPASSRLS role) is inherited, not reinvented.
"""

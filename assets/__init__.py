# Marker — makes setuptools ship the KDTIX issue-body templates
# (template-scope.md, template-initiative.md, template-epic.md,
# template-story.md, template-task.md) as package data so
# scripts.create_issues._load_template() can locate them after a
# `pip install` (including the pip-install-from-GitHub used by the
# hosted SBR MCP Docker image).
#
# Before 2026-04-23 this directory shipped only in source checkouts.
# The hosted container had no templates on disk, so _load_template
# returned "" and generate_body fell through to _body_scope (which
# emits placeholder stubs).  Issue #182's write-back body was blanked
# from 9,977 → 1,088 chars as a result.  Making `assets` a package
# keeps the templates bundled; the WriteBacker itself was also
# rewritten to use a surgical section-replace strategy that doesn't
# depend on templates.  See scripts/sbr/api.py WriteBacker for the
# post-incident design.

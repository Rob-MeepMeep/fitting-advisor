"""Fit legality validation.

Delegates engine-level attribute and rule checks to ``advisor.engine.validate_fit_eos``
so that ``eos`` stays isolated in ``advisor.engine``.
"""

from __future__ import annotations

from advisor import engine
from advisor.models import FitSpec, SkillSet, ValidationResult


def validate_fit(fit_spec: FitSpec, skills: SkillSet | None = None) -> ValidationResult:
    """Validate a fit and return ALL legality violations."""
    issues = engine.validate_fit_eos(fit_spec, skills)
    return ValidationResult(valid=len(issues) == 0, issues=issues)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Set

from jinja2 import Environment, StrictUndefined, meta

from app.core.template_vars import ALLOWED_TEMPLATE_VARS


@dataclass
class RenderedEmail:
    subject: str
    body: str
    used_vars: Set[str]


def _make_env() -> Environment:
    return Environment(
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _extract_vars(env: Environment, text: str) -> Set[str]:
    ast = env.parse(text or "")
    return set(meta.find_undeclared_variables(ast))


def render_email_template(*, subject_tpl: str, body_tpl: str, context: Dict[str, Any]) -> RenderedEmail:
    env = _make_env()

    subject_vars = _extract_vars(env, subject_tpl)
    body_vars = _extract_vars(env, body_tpl)
    used_vars = subject_vars.union(body_vars)

    invalid = used_vars - ALLOWED_TEMPLATE_VARS
    if invalid:
        raise ValueError(f"Template usa variáveis não permitidas: {sorted(invalid)}")

    subject = env.from_string(subject_tpl or "").render(**context)
    body = env.from_string(body_tpl or "").render(**context)

    return RenderedEmail(subject=subject, body=body, used_vars=used_vars)

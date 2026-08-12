from __future__ import annotations

from research_deid.crypto import (
    derivation_context,
    derive_shift_weeks,
    derive_token,
    jitter_candidates,
)


def test_keyed_derivations_are_stable_and_domain_separated() -> None:
    secret = bytes(range(32))
    context = derivation_context(
        collaboration_id="c",
        schema_version="s1",
        key_alias="k",
        key_version="1",
    )
    first = derive_token(secret, context, domain="participant", prefix="p", components=["GUID-1"])
    assert first == derive_token(secret, context, domain="participant", prefix="p", components=["GUID-1"])
    assert first != derive_token(secret, context, domain="dyad", prefix="d", components=["GUID-1"])
    shift = derive_shift_weeks(secret, context, scope="participant", components=["GUID-1"])
    assert 4 <= abs(shift) <= 52
    candidates = list(jitter_candidates(secret, context, group="ema", anchor_components=["A1"]))
    assert len(candidates) == 361
    assert sorted(candidates) == list(range(-180, 181))

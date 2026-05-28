"""Tests for ${KEY}-style secret substitution."""

from __future__ import annotations

import pytest

from src.credentials.exceptions import CredentialNotFound
from src.credentials.substitution import (
    SECRET_REFERENCE_PATTERN,
    iter_secret_references,
    resolve_secrets,
)


def _resolver(values: dict[str, str]):
    def _lookup(key: str) -> str:
        if key in values:
            return values[key]
        raise CredentialNotFound(key)

    return _lookup


class TestSubstitutionGrammar:
    def test_simple_reference_resolved(self) -> None:
        result = resolve_secrets("${OPENAI_KEY}", _resolver({"OPENAI_KEY": "sk-x"}))
        assert result == "sk-x"

    def test_reference_inside_larger_string(self) -> None:
        result = resolve_secrets(
            "Bearer ${TOKEN}",
            _resolver({"TOKEN": "abc"}),
        )
        assert result == "Bearer abc"

    def test_multiple_references_in_one_string(self) -> None:
        result = resolve_secrets(
            "${USER}:${PASS}",
            _resolver({"USER": "admin", "PASS": "topsecret"}),
        )
        assert result == "admin:topsecret"

    def test_dollar_dollar_escapes_to_literal_dollar(self) -> None:
        """``$${KEY}`` becomes literal ``${KEY}``."""
        result = resolve_secrets("$${OPENAI_KEY}", _resolver({}))
        assert result == "${OPENAI_KEY}"

    def test_dollar_dollar_escape_around_real_substitution(self) -> None:
        result = resolve_secrets(
            "$${LITERAL}-${REAL}",
            _resolver({"REAL": "value"}),
        )
        assert result == "${LITERAL}-value"

    def test_lowercase_keys_are_left_alone(self) -> None:
        """``${log.level}`` does not match the strict grammar."""
        result = resolve_secrets("${log.level}", _resolver({}))
        assert result == "${log.level}"

    def test_keys_starting_with_digit_left_alone(self) -> None:
        """``${1KEY}`` does not match (must start with letter)."""
        result = resolve_secrets("${1KEY}", _resolver({}))
        assert result == "${1KEY}"

    def test_lone_dollar_left_alone(self) -> None:
        result = resolve_secrets("$5 dollars", _resolver({}))
        assert result == "$5 dollars"

    def test_unmatched_brace_left_alone(self) -> None:
        """``${UNCLOSED`` is not a valid reference; left untouched."""
        result = resolve_secrets("${UNCLOSED rest", _resolver({}))
        assert result == "${UNCLOSED rest"

    def test_empty_string_returned_unchanged(self) -> None:
        assert resolve_secrets("", _resolver({})) == ""


class TestSubstitutionRecursive:
    def test_dict_walk(self) -> None:
        cfg = {
            "actor": {
                "name": "llm.openai",
                "api_key": "${OPENAI_KEY}",
                "provider": "openai",
            }
        }
        result = resolve_secrets(cfg, _resolver({"OPENAI_KEY": "sk-test"}))
        assert result == {
            "actor": {
                "name": "llm.openai",
                "api_key": "sk-test",
                "provider": "openai",
            }
        }

    def test_list_walk(self) -> None:
        cfg = ["${A}", "literal", "${B}"]
        result = resolve_secrets(cfg, _resolver({"A": "alpha", "B": "beta"}))
        assert result == ["alpha", "literal", "beta"]

    def test_tuple_walk_returns_tuple(self) -> None:
        result = resolve_secrets(("${A}", "x"), _resolver({"A": "1"}))
        assert isinstance(result, tuple)
        assert result == ("1", "x")

    def test_nested_structure(self) -> None:
        cfg = {
            "channels": [
                {"name": "websocket", "token": "${WS_TOKEN}"},
                {"name": "telegram", "token": "${TG_TOKEN}"},
            ]
        }
        result = resolve_secrets(
            cfg,
            _resolver({"WS_TOKEN": "ws-x", "TG_TOKEN": "tg-y"}),
        )
        assert result["channels"][0]["token"] == "ws-x"
        assert result["channels"][1]["token"] == "tg-y"

    def test_non_string_primitives_pass_through(self) -> None:
        cfg = {"port": 8080, "enabled": True, "ratio": 0.5, "missing": None}
        result = resolve_secrets(cfg, _resolver({}))
        assert result == cfg

    def test_input_is_not_mutated(self) -> None:
        cfg = {"key": "${VAL}"}
        original = dict(cfg)
        resolve_secrets(cfg, _resolver({"VAL": "x"}))
        assert cfg == original


class TestSubstitutionFailFast:
    def test_unresolved_key_raises_credential_not_found(self) -> None:
        with pytest.raises(CredentialNotFound) as excinfo:
            resolve_secrets("${MISSING}", _resolver({}))
        assert excinfo.value.key == "MISSING"

    def test_unresolved_key_in_nested_structure_aborts(self) -> None:
        cfg = {"actor": {"api_key": "${OPENAI_KEY}"}}
        with pytest.raises(CredentialNotFound):
            resolve_secrets(cfg, _resolver({}))

    def test_resolver_arbitrary_exception_wrapped(self) -> None:
        def boom(key: str) -> str:
            raise RuntimeError("backend boom")

        with pytest.raises(CredentialNotFound):
            resolve_secrets("${X}", boom)


class TestIterSecretReferences:
    def test_iterates_in_traversal_order(self) -> None:
        cfg = {
            "a": "${FIRST}",
            "b": ["literal", "${SECOND}"],
            "c": {"nested": "${THIRD}"},
        }
        refs = list(iter_secret_references(cfg))
        assert refs == ["FIRST", "SECOND", "THIRD"]

    def test_escaped_references_are_not_yielded(self) -> None:
        cfg = "$${ESCAPED} and ${REAL}"
        refs = list(iter_secret_references(cfg))
        assert refs == ["REAL"]

    def test_no_refs_returns_empty(self) -> None:
        assert list(iter_secret_references({"a": 1, "b": "literal"})) == []


class TestSecretReferencePattern:
    def test_pattern_matches_expected_keys(self) -> None:
        assert SECRET_REFERENCE_PATTERN.fullmatch("${OPENAI_KEY}") is not None
        assert SECRET_REFERENCE_PATTERN.fullmatch("${A}") is not None
        assert SECRET_REFERENCE_PATTERN.fullmatch("${A1B2}") is not None

    def test_pattern_rejects_lowercase(self) -> None:
        assert SECRET_REFERENCE_PATTERN.fullmatch("${lower}") is None

    def test_pattern_rejects_dotted(self) -> None:
        assert SECRET_REFERENCE_PATTERN.fullmatch("${A.B}") is None

    def test_pattern_rejects_leading_digit(self) -> None:
        assert SECRET_REFERENCE_PATTERN.fullmatch("${1KEY}") is None

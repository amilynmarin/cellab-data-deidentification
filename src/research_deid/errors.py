from __future__ import annotations


class DeidError(Exception):
    """Base exception with a stable CLI exit code."""

    exit_code = 1


class SchemaError(DeidError):
    exit_code = 2


class InputDataError(DeidError):
    exit_code = 3


class DataValidationError(DeidError):
    exit_code = 4


class KeyManagementError(DeidError):
    exit_code = 5


class OutputError(DeidError):
    exit_code = 6


class ReproductionError(DeidError):
    exit_code = 7


class TransformationError(DeidError):
    exit_code = 7

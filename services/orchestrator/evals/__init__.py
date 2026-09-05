"""Shipwright AI evals (plan 12 §5) — measure whether each agent is good at its job.

Datasets live under ``datasets/<suite>/`` (an ``EvalSuite`` manifest + ``EvalCase`` files);
the harness under ``harness/`` loads a suite, invokes the agent under test with the
deterministic offline provider, scores each case (objective scorers preferred over judges),
aggregates over N samples, and gates on the suite mean.
"""

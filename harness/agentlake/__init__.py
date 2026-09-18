"""agentlake — a harness for characterizing multi-agent workloads on a lakehouse.

The harness drives a configurable fleet of analytical agents against a lakehouse
under test and instruments the *platform* side: scan amplification, cache
behaviour, catalog request skew, and cost per answered question.

It deliberately does not measure answer accuracy. That is well covered elsewhere.
"""

__version__ = "0.1.0"

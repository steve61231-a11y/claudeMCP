"""Staged analysis agents.

Each agent is a narrow, resumable stage over the acquired corpus. They are thin
orchestrators: the real work lives in engine/processing, engine/intelligence and
engine/reports, and the agents sequence it, batch it, and record what was done so
a stage never repeats work or silently skips items.
"""

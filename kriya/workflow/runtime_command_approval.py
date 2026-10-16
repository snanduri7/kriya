"""Facade (full modularization, slice C3): the runtime-command approval subject, fingerprint, reviewability rule, typed decision and callback
moved unchanged to their AUTHORITY owner ``kriya/policy/approval.py`` (GUI-RUNTIME-COMMAND-APPROVAL-001; the module documentation moved
with them). This old import path is kept for its importers (tests); every name is the owner's object. Removed in P1."""
from kriya.policy.approval import MAX_ARGV_CHARS as MAX_ARGV_CHARS
from kriya.policy.approval import MAX_COMMANDS as MAX_COMMANDS
from kriya.policy.approval import MAX_REVIEW_ROUNDS as MAX_REVIEW_ROUNDS
from kriya.policy.approval import REVIEW_KIND as REVIEW_KIND
from kriya.policy.approval import RuntimeCommandDecision as RuntimeCommandDecision
from kriya.policy.approval import RuntimeCommandSubject as RuntimeCommandSubject
from kriya.policy.approval import TypedApprovalCallback as TypedApprovalCallback
from kriya.policy.approval import approval_matches as approval_matches
from kriya.policy.approval import ask as ask
from kriya.policy.approval import build_subject as build_subject
from kriya.policy.approval import commands_authored_in_goal as commands_authored_in_goal
from kriya.policy.approval import fingerprint_of as fingerprint_of
from kriya.policy.approval import reviewable as reviewable
from kriya.policy.approval import stdin_digest as stdin_digest

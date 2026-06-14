"""Brokerage / account-aggregation integration (Phase 3.1 - scaffold).

A vendor-agnostic seam for replacing manual portfolio entry with live holdings
sync. Read-only aggregation (Plaid / Yodlee style) is the first step; order
placement (the existing Israeli-broker plan) layers on the same abstraction
later. Everything here runs against a deterministic ``mock`` adapter so the
whole flow is testable with zero real credentials and nothing moves real money.
"""

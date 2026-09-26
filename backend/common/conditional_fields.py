"""
Conditional field requirements configuration.

Reference configuration for which fields are typically required
based on status. These are NOT enforced at the model
level but can be used by serializers or frontend for validation hints.
"""

# Lead: Suggested required fields by status
LEAD_REQUIRED_BY_STATUS = {
    "assigned": [],
    "in process": [],
    "converted": ["email"],  # Only email is enforced
    "recycled": [],
    "closed": [],
}

# Case: Suggested required fields by status
CASE_REQUIRED_BY_STATUS = {
    "New": ["name", "status", "priority"],
    "Assigned": ["name", "status", "priority"],
    "Pending": ["name", "status", "priority"],
    "Closed": ["name", "status", "priority", "closed_on"],
    "Rejected": ["name", "status", "priority"],
    "Duplicate": ["name", "status", "priority"],
}

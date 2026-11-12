"""The ORM classes whose columns are encrypted with CipherStash.

The API, the worker and the tests pass this list to ``configure_encryption``.
One list, so no process encrypts a class that another process cannot decrypt.
"""

from typing import Any


def classes() -> list[Any]:
    """Return the classes to pass to ``configure_encryption``.

    The imports are inside the function, so this module can be imported
    before the models are.
    """
    import modules.activities.activity.models as activity_models
    import modules.users.users.models as users_models

    return [users_models.Users, activity_models.Activity]

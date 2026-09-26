"""Board membership receivers. Connected by `TasksConfig.ready`."""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from tasks.models import Board, BoardMember, BoardTask


@receiver(post_delete, sender=BoardMember)
def unassign_removed_member(sender, instance, **kwargs):
    """Take someone off a board's cards when they leave the board.

    `_set_card_assignees` only lets a card be assigned to someone who can open
    its board (the owner or a member), but that was checked at write time, so
    a member removed later stayed on every card they held until the next
    assignee write, and kept being shown as its assignee. `post_delete` fires
    for every way the row goes: an instance or queryset delete, the admin,
    `board.members.remove()`/`.clear()`/`.set()`, and the cascade from
    deleting the profile or the board. It runs inside the delete's own
    transaction, so the membership and the assignments go together.

    The board's owner can open it without a membership row, so losing that
    row leaves their cards alone.
    """
    if Board.objects.filter(
        pk=instance.board_id, owner_id=instance.profile_id
    ).exists():
        return
    BoardTask.assigned_to.through.objects.filter(
        profile_id=instance.profile_id,
        boardtask__column__board_id=instance.board_id,
    ).delete()

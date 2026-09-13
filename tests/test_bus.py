"""Bus routing, history and message protocol tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentteam.bus import InMemoryBus, Subscription, addressed_to
from agentteam.schemas import Message, MessageType, Role, make_message, recipient_label


def _message(sender: Role, recipient: Role | str, type: MessageType = MessageType.LOG) -> Message:
    return make_message(sender=sender, recipient=recipient, type=type, payload={"summary": "x"})


def test_addressed_to_matches_direct_and_broadcast() -> None:
    direct = _message(Role.PLANNER, Role.ACTOR)
    broadcast = _message(Role.PLANNER, "broadcast")

    assert addressed_to(direct, Role.ACTOR)
    assert not addressed_to(direct, Role.TESTER)
    assert addressed_to(broadcast, Role.REVIEWER)
    assert addressed_to(broadcast, "reviewer")


def test_recipient_label_never_leaks_enum_repr() -> None:
    assert recipient_label(_message(Role.PLANNER, Role.ACTOR)) == "actor"
    assert recipient_label(_message(Role.PLANNER, "broadcast")) == "broadcast"


def test_message_summary_and_envelope() -> None:
    message = _message(Role.ACTOR, Role.REVIEWER, MessageType.ACT)
    envelope = message.to_envelope()

    assert set(envelope) == {"id", "from", "to", "type", "round", "payload", "created_at"}
    assert envelope["from"] == "actor"
    assert envelope["to"] == "reviewer"
    assert len(Message(sender=Role.SYSTEM, recipient="broadcast", type=MessageType.LOG).summary()) > 0


def test_inbox_filters_by_recipient(run: Callable[[Any], Any]) -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        bus = InMemoryBus()
        planner_inbox = bus.inbox(Role.PLANNER)
        actor_inbox = bus.inbox(Role.ACTOR)
        await bus.publish(_message(Role.USER, Role.PLANNER, MessageType.TASK))
        await bus.publish(_message(Role.USER, "broadcast"))
        planner_seen = [(await planner_inbox.get()).sender.value, (await planner_inbox.get()).sender.value]
        actor_seen = [(await actor_inbox.get()).sender.value]
        await bus.close()
        return planner_seen, actor_seen

    planner_seen, actor_seen = run(scenario())
    assert planner_seen == ["user", "user"]
    assert actor_seen == ["user"]


def test_firehose_and_history_see_everything(run: Callable[[Any], Any]) -> None:
    async def scenario() -> tuple[int, list[str]]:
        bus = InMemoryBus()
        firehose = bus.firehose()
        await bus.publish(_message(Role.PLANNER, Role.ACTOR, MessageType.PLAN))
        await bus.publish(_message(Role.TESTER, Role.ORCHESTRATOR, MessageType.TEST))
        first = await firehose.get()
        second = await firehose.get()
        history = await bus.history()
        await bus.close()
        return (first, second) and len(history), [item.type.value for item in history]

    count, types = run(scenario())
    assert count == 2
    assert types == ["plan", "test"]


def test_closed_subscription_returns_none(run: Callable[[Any], Any]) -> None:
    async def scenario() -> Message | None:
        subscription = Subscription("test")
        subscription.close()
        return await subscription.get()

    assert run(scenario()) is None

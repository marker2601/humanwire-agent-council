from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from humanwire.domain import Channel, ContactRoute, Direction, IncomingMessage, Person


class UnknownPersonError(ValueError):
    """Raised when a directory reference cannot be resolved."""


class AmbiguousPersonError(ValueError):
    """Raised when a directory reference identifies multiple people."""


class UnauthorizedTargetError(ValueError):
    """Raised when an initiator is not allowed to contact a target."""


class InitiatorPolicy(BaseModel):
    person_id: str
    allowed_directions: set[Direction]
    allowed_departments: set[str]
    max_upward_levels: int = Field(default=1, ge=0, le=5)


class OrganizationDocument(BaseModel):
    people: list[Person]
    initiator_policies: list[InitiatorPolicy]


class OrganizationDirectory:
    def __init__(self, document: OrganizationDocument) -> None:
        self.document = document
        self._people_by_id = {person.person_id.casefold(): person for person in document.people}
        self._policies_by_person_id = {
            policy.person_id.casefold(): policy for policy in document.initiator_policies
        }
        self._people_by_reference: dict[str, list[Person]] = defaultdict(list)
        self._people_by_sender: dict[tuple[Channel, str], list[Person]] = defaultdict(list)

        for person in document.people:
            for reference in (person.person_id, person.display_name, *person.aliases):
                self._people_by_reference[reference.casefold()].append(person)
            for route in person.routes:
                self._people_by_sender[(route.channel, route.sender_address.casefold())].append(
                    person
                )

    @classmethod
    def load(cls, path: Path | str) -> OrganizationDirectory:
        document = OrganizationDocument.model_validate_json(Path(path).read_text(encoding="utf-8"))
        return cls(document)

    def resolve_person(self, ref: str) -> Person:
        people = self._unique_people(self._people_by_reference.get(ref.casefold(), []))
        if not people:
            raise UnknownPersonError(f"No person matches {ref!r}")
        if len(people) > 1:
            raise AmbiguousPersonError(f"Multiple people match {ref!r}")
        return people[0]

    def person_for_sender(self, message: IncomingMessage) -> Person:
        people = self._unique_people(
            self._people_by_sender.get((message.channel, message.sender_address.casefold()), [])
        )
        if not people:
            raise UnknownPersonError("No person matches the incoming sender")
        if len(people) > 1:
            raise AmbiguousPersonError("Multiple people match the incoming sender")
        return people[0]

    def is_authorized_initiator(self, message: IncomingMessage) -> bool:
        try:
            person = self.person_for_sender(message)
        except (UnknownPersonError, AmbiguousPersonError):
            return False
        return person.person_id.casefold() in self._policies_by_person_id

    def classify_direction(self, initiator_id: str, target_id: str) -> Direction:
        initiator = self.resolve_person(initiator_id)
        target = self.resolve_person(target_id)

        if initiator.person_id.casefold() in self._manager_chain_ids(target):
            return Direction.DOWNWARD
        if target.person_id.casefold() in self._manager_chain_ids(initiator):
            return Direction.UPWARD
        return Direction.LATERAL

    def validate_target(
        self,
        initiator_id: str,
        target_id: str,
        requested_direction: Direction,
    ) -> Person:
        initiator = self.resolve_person(initiator_id)
        target = self.resolve_person(target_id)
        policy = self._policies_by_person_id.get(initiator.person_id.casefold())
        actual_direction = self.classify_direction(initiator.person_id, target.person_id)

        if actual_direction is not requested_direction:
            raise UnauthorizedTargetError("Requested direction does not match the organization")
        if policy is None or actual_direction not in policy.allowed_directions:
            raise UnauthorizedTargetError("Initiator is not allowed to contact in this direction")
        if target.department not in policy.allowed_departments:
            raise UnauthorizedTargetError("Initiator is not allowed to contact this department")
        if actual_direction is Direction.UPWARD:
            upward_levels = len(self._manager_chain_to(initiator, target.person_id))
            if upward_levels > policy.max_upward_levels:
                raise UnauthorizedTargetError("Target exceeds the initiator's upward authority")
        return target

    def ordered_routes(self, person_id: str) -> list[ContactRoute]:
        person = self.resolve_person(person_id)
        routes = [
            route
            for route in person.routes
            if (route.channel is Channel.EMAIL and route.recipient)
            or (route.channel is Channel.TELEGRAM and route.conversation_id)
        ]
        return sorted(routes, key=lambda route: not route.preferred)

    def _manager_chain_ids(self, person: Person) -> set[str]:
        visited: set[str] = set()
        current = person
        while current.manager_id:
            manager_id = current.manager_id.casefold()
            if manager_id in visited:
                break
            visited.add(manager_id)
            manager = self._people_by_id.get(manager_id)
            if manager is None:
                break
            current = manager
        return visited

    def _manager_chain_to(self, person: Person, target_id: str) -> list[str]:
        target_key = target_id.casefold()
        path: list[str] = []
        visited: set[str] = set()
        current = person
        while current.manager_id:
            manager_id = current.manager_id.casefold()
            if manager_id in visited:
                break
            visited.add(manager_id)
            path.append(manager_id)
            if manager_id == target_key:
                return path
            manager = self._people_by_id.get(manager_id)
            if manager is None:
                break
            current = manager
        return []

    @staticmethod
    def _unique_people(people: list[Person]) -> list[Person]:
        return list({person.person_id.casefold(): person for person in people}.values())

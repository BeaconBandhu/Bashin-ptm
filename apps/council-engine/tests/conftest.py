from decimal import Decimal

import pytest

from app.audit.store import InMemoryAuditStore
from app.concurrency.idempotency import InMemoryIdempotencyStore

# Import (once) so the @register_node_type decorators run before any test
# touches the node registry / validator / graph builder.
from app.domains.payment import nodes as _payment_nodes  # noqa: F401
from app.domains.payment.wiring import PaymentDependencies, build_node_instances
from app.mocks.bank import MockBankClient
from app.mocks.crm import MockCRMClient
from app.mocks.payment_rail import MockPaymentRailClient


@pytest.fixture
def bank() -> MockBankClient:
    client = MockBankClient()
    client.seed_account("cust-1", Decimal(75000), "INR")
    return client


@pytest.fixture
def crm() -> MockCRMClient:
    client = MockCRMClient()
    client.verify_payee("cust-1", "payee-1")
    return client


@pytest.fixture
def payment_rail() -> MockPaymentRailClient:
    return MockPaymentRailClient()


@pytest.fixture
def idempotency_store() -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore()


@pytest.fixture
def audit_store() -> InMemoryAuditStore:
    return InMemoryAuditStore()


@pytest.fixture
def node_instances(bank, payment_rail, crm, idempotency_store):
    deps = PaymentDependencies(
        bank=bank, payment_rail=payment_rail, crm=crm, idempotency_store=idempotency_store
    )
    return build_node_instances(deps)


# ---- customer-support domain fixtures --------------------------------------

from app.domains.support import nodes as _support_nodes  # noqa: F401
from app.mocks.human_agents import MockHumanAgentQueue
from app.rag.retriever import TfidfRetriever
from app.spend.tracker import InMemorySpendTracker
from app.tickets.memory_store import InMemoryTicketStore


@pytest.fixture
def retriever() -> TfidfRetriever:
    return TfidfRetriever()


@pytest.fixture
def support_ticket_store() -> InMemoryTicketStore:
    return InMemoryTicketStore()


@pytest.fixture
def spend_tracker() -> InMemorySpendTracker:
    return InMemorySpendTracker()


@pytest.fixture
def agent_queue() -> MockHumanAgentQueue:
    return MockHumanAgentQueue()

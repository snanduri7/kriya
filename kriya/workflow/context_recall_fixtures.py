"""PRD-027: version-controlled context-recall certification fixtures.

Two small repositories, one Java and one Python. Each has a set of cases:
- a goal;
- the golden context items the Developer genuinely needs for that goal,
  with each item's context class and the minimum precision tier at which
  it is useful;
- ``acceptable`` files, which are relevant but not required. They do not
  count against precision.
Every other file is a distractor.

The fixtures are Kriya-owned test data. They are used only by
``kriya/workflow/context_certification.py`` and are never consulted at
runtime, so no hidden golden data can influence retrieval. Changing them
changes ``FIXTURES_DIGEST`` and so invalidates every stored certification.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Dict, Tuple

# Context classes (PRD-027 requirement 1).
SAME_CLASS_MEMBER = "same_class_member"
SIBLING_IMPLEMENTATION = "sibling_implementation"
INTERFACE_CONTRACT = "interface_contract"
TEST_PRECEDENT = "test_precedent"
BUILD_METADATA = "build_metadata"
CONFIGURATION = "configuration"
DIRECT_CALLER = "direct_caller"
ONE_HOP_DEPENDENCY = "one_hop_dependency"
TWO_HOP_DEPENDENCY = "two_hop_dependency"
CONTEXT_CLASSES = (
    SAME_CLASS_MEMBER, SIBLING_IMPLEMENTATION, INTERFACE_CONTRACT, TEST_PRECEDENT,
    BUILD_METADATA, CONFIGURATION, DIRECT_CALLER, ONE_HOP_DEPENDENCY, TWO_HOP_DEPENDENCY,
)


@dataclass(frozen=True)
class GoldenItem:
    path: str
    context_class: str
    # Minimum useful tier: "full" (bodies needed), "skeleton" (structure
    # plus signatures) or "signatures" (the declared API is enough).
    precision: str


@dataclass(frozen=True)
class RecallCase:
    name: str
    goal: str
    golden: Tuple[GoldenItem, ...]
    acceptable: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RecallRepository:
    name: str
    files: Tuple[Tuple[str, str], ...]
    cases: Tuple[RecallCase, ...]


_J = "src/main/java/com/shop"

JAVA_SHOP = RecallRepository(
    name="java-shop",
    files=(
        ("pom.xml", """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.shop</groupId>
  <artifactId>shop-orders</artifactId>
  <version>1.0.0</version>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.9.3</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""),
        ("src/main/resources/application.properties", """# Order service settings
order.discount.max-percent=50
order.currency=USD
order.cancellation.window-hours=24
"""),
        (f"{_J}/order/Order.java", """package com.shop.order;

import java.math.BigDecimal;

public record Order(String id, String customerId, BigDecimal subtotal, boolean loyalCustomer) {
}
"""),
        (f"{_J}/order/DiscountPolicy.java", """package com.shop.order;

import java.math.BigDecimal;

/** Contract every discount strategy implements. */
public interface DiscountPolicy {
    BigDecimal discountFor(Order order);
}
"""),
        (f"{_J}/order/OrderService.java", """package com.shop.order;

import com.shop.pricing.PriceCalculator;
import java.math.BigDecimal;
import java.util.HashMap;
import java.util.Map;

public class OrderService implements DiscountPolicy {
    private final PriceCalculator priceCalculator;
    private final Map<String, Order> orders = new HashMap<>();

    public OrderService(PriceCalculator priceCalculator) {
        this.priceCalculator = priceCalculator;
    }

    public Order placeOrder(Order order) {
        orders.put(order.id(), order);
        return order;
    }

    public boolean cancelOrder(String orderId) {
        return orders.remove(orderId) != null;
    }

    public BigDecimal computeDiscount(Order order) {
        BigDecimal total = priceCalculator.totalWithTax(order.subtotal());
        BigDecimal rate = order.loyalCustomer() ? new BigDecimal("0.60") : new BigDecimal("0.10");
        return total.multiply(rate);
    }

    @Override
    public BigDecimal discountFor(Order order) {
        return computeDiscount(order);
    }
}
"""),
        (f"{_J}/order/SeasonalDiscountPolicy.java", """package com.shop.order;

import java.math.BigDecimal;

public class SeasonalDiscountPolicy implements DiscountPolicy {
    private final BigDecimal seasonalRate;

    public SeasonalDiscountPolicy(BigDecimal seasonalRate) {
        this.seasonalRate = seasonalRate;
    }

    @Override
    public BigDecimal discountFor(Order order) {
        return order.subtotal().multiply(seasonalRate).min(order.subtotal().multiply(new BigDecimal("0.5")));
    }
}
"""),
        (f"{_J}/order/OrderController.java", """package com.shop.order;

import java.math.BigDecimal;

public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    public String quote(Order order) {
        BigDecimal discount = orderService.computeDiscount(order);
        return "Discount for " + order.id() + ": " + discount;
    }
}
"""),
        (f"{_J}/pricing/PriceCalculator.java", """package com.shop.pricing;

import java.math.BigDecimal;

public class PriceCalculator {
    private final TaxTable taxTable;

    public PriceCalculator(TaxTable taxTable) {
        this.taxTable = taxTable;
    }

    public BigDecimal totalWithTax(BigDecimal subtotal) {
        return subtotal.add(subtotal.multiply(taxTable.rateFor("default")));
    }
}
"""),
        (f"{_J}/pricing/TaxTable.java", """package com.shop.pricing;

import java.math.BigDecimal;
import java.util.Map;

public class TaxTable {
    private final Map<String, BigDecimal> rates = Map.of("default", new BigDecimal("0.08"));

    public BigDecimal rateFor(String region) {
        return rates.getOrDefault(region, BigDecimal.ZERO);
    }
}
"""),
        ("src/test/java/com/shop/order/OrderServiceTest.java", """package com.shop.order;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.shop.pricing.PriceCalculator;
import com.shop.pricing.TaxTable;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class OrderServiceTest {
    @Test
    void computeDiscountForRegularCustomer() {
        OrderService service = new OrderService(new PriceCalculator(new TaxTable()));
        Order order = new Order("o-1", "c-1", new BigDecimal("100"), false);
        assertEquals(0, new BigDecimal("10.80").compareTo(service.computeDiscount(order)));
    }
}
"""),
        (f"{_J}/inventory/StockLevel.java", """package com.shop.inventory;

public class StockLevel {
    private int onHand;

    public void receive(int quantity) {
        onHand += quantity;
    }

    public boolean reserve(int quantity) {
        if (quantity > onHand) {
            return false;
        }
        onHand -= quantity;
        return true;
    }
}
"""),
        (f"{_J}/inventory/WarehouseClient.java", """package com.shop.inventory;

import java.util.List;

public class WarehouseClient {
    public List<String> listWarehouses() {
        return List.of("east", "west");
    }

    public int stockAt(String warehouse, String sku) {
        return warehouse.length() + sku.length();
    }
}
"""),
        (f"{_J}/notify/EmailSender.java", """package com.shop.notify;

public class EmailSender {
    public String send(String recipient, String subject, String body) {
        return recipient + ":" + subject + ":" + body.length();
    }
}
"""),
        (f"{_J}/notify/SmsSender.java", """package com.shop.notify;

public class SmsSender {
    public String send(String phoneNumber, String message) {
        return phoneNumber + ":" + message;
    }
}
"""),
    ),
    cases=(
        RecallCase(
            name="discount-cap",
            goal="Cap the discount returned by OrderService.computeDiscount at 50 percent of the order subtotal.",
            golden=(
                GoldenItem(f"{_J}/order/OrderService.java", SAME_CLASS_MEMBER, "full"),
                GoldenItem(f"{_J}/order/DiscountPolicy.java", INTERFACE_CONTRACT, "signatures"),
                GoldenItem(f"{_J}/order/SeasonalDiscountPolicy.java", SIBLING_IMPLEMENTATION, "signatures"),
                GoldenItem(f"{_J}/order/OrderController.java", DIRECT_CALLER, "skeleton"),
                GoldenItem(f"{_J}/pricing/PriceCalculator.java", ONE_HOP_DEPENDENCY, "signatures"),
                GoldenItem(f"{_J}/pricing/TaxTable.java", TWO_HOP_DEPENDENCY, "signatures"),
                GoldenItem("src/test/java/com/shop/order/OrderServiceTest.java", TEST_PRECEDENT, "skeleton"),
            ),
            acceptable=(f"{_J}/order/Order.java",),
        ),
        RecallCase(
            name="discount-from-config",
            goal=(
                "Read the maximum discount percent for OrderService from the order.discount.max-percent "
                "setting in application.properties instead of hard-coding it."
            ),
            golden=(
                GoldenItem("src/main/resources/application.properties", CONFIGURATION, "full"),
                GoldenItem(f"{_J}/order/OrderService.java", SAME_CLASS_MEMBER, "full"),
            ),
            acceptable=(f"{_J}/order/Order.java", f"{_J}/order/DiscountPolicy.java"),
        ),
        RecallCase(
            name="junit-upgrade",
            goal="Upgrade the junit-jupiter test dependency in the Maven pom.xml to version 5.10.2.",
            golden=(GoldenItem("pom.xml", BUILD_METADATA, "full"),),
            acceptable=("src/test/java/com/shop/order/OrderServiceTest.java",),
        ),
    ),
)


PYTHON_PAYMENTS = RecallRepository(
    name="python-payments",
    files=(
        ("pyproject.toml", """[project]
name = "payments"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = ["requests>=2.31"]

[project.optional-dependencies]
test = ["pytest>=7.4"]
"""),
        ("config/settings.yaml", """payments:
  max_charge_retries: 3
  default_currency: USD
  retry_backoff_seconds: 2
"""),
        ("payments/__init__.py", ""),
        ("payments/gateway.py", '''"""Payment gateway contract."""
from abc import ABC, abstractmethod


class PaymentGateway(ABC):
    @abstractmethod
    def charge(self, amount_cents: int, currency: str) -> str:
        """Charge the customer and return a transaction id."""

    @abstractmethod
    def refund(self, transaction_id: str) -> bool:
        """Refund a previous charge."""
'''),
        ("payments/stripe_gateway.py", '''from payments.currency import to_minor_units
from payments.gateway import PaymentGateway


class StripeGateway(PaymentGateway):
    def __init__(self, client):
        self.client = client

    def charge(self, amount_cents: int, currency: str) -> str:
        minor = to_minor_units(amount_cents, currency)
        response = self.client.post("/charges", {"amount": minor, "currency": currency})
        return response["id"]

    def refund(self, transaction_id: str) -> bool:
        return self.client.post("/refunds", {"charge": transaction_id})["ok"]
'''),
        ("payments/paypal_gateway.py", '''from payments.gateway import PaymentGateway


class PaypalGateway(PaymentGateway):
    def __init__(self, session):
        self.session = session

    def charge(self, amount_cents: int, currency: str) -> str:
        return self.session.pay(amount_cents, currency)

    def refund(self, transaction_id: str) -> bool:
        return self.session.reverse(transaction_id)
'''),
        ("payments/checkout.py", '''from payments.stripe_gateway import StripeGateway


def complete_checkout(gateway: StripeGateway, cart_total_cents: int) -> str:
    transaction_id = gateway.charge(cart_total_cents, "USD")
    return f"checkout complete: {transaction_id}"
'''),
        ("payments/currency.py", '''from payments.rates import rate_for


def to_minor_units(amount_cents: int, currency: str) -> int:
    return int(amount_cents * rate_for(currency))
'''),
        ("payments/rates.py", '''RATES = {"USD": 1.0, "EUR": 0.92}


def rate_for(currency: str) -> float:
    return RATES.get(currency, 1.0)
'''),
        ("tests/test_stripe_gateway.py", '''from payments.stripe_gateway import StripeGateway


class FakeClient:
    def post(self, path, payload):
        return {"id": "ch_1", "ok": True}


def test_charge_returns_transaction_id():
    assert StripeGateway(FakeClient()).charge(500, "USD") == "ch_1"
'''),
        ("payments/emailer.py", '''def send_receipt(address: str, body: str) -> str:
    return f"{address}:{len(body)}"
'''),
        ("inventory/__init__.py", ""),
        ("inventory/stock.py", '''class Stock:
    def __init__(self):
        self.on_hand = 0

    def receive(self, quantity: int) -> None:
        self.on_hand += quantity
'''),
        ("inventory/warehouse.py", '''def list_warehouses() -> list:
    return ["east", "west"]
'''),
    ),
    cases=(
        RecallCase(
            name="charge-retry",
            goal="Make StripeGateway.charge retry a failed charge request up to three times before raising.",
            golden=(
                GoldenItem("payments/stripe_gateway.py", SAME_CLASS_MEMBER, "full"),
                GoldenItem("payments/gateway.py", INTERFACE_CONTRACT, "signatures"),
                GoldenItem("payments/paypal_gateway.py", SIBLING_IMPLEMENTATION, "signatures"),
                GoldenItem("payments/checkout.py", DIRECT_CALLER, "skeleton"),
                GoldenItem("payments/currency.py", ONE_HOP_DEPENDENCY, "signatures"),
                GoldenItem("payments/rates.py", TWO_HOP_DEPENDENCY, "signatures"),
                GoldenItem("tests/test_stripe_gateway.py", TEST_PRECEDENT, "skeleton"),
            ),
        ),
        RecallCase(
            name="retries-from-config",
            goal=(
                "Read the charge retry count for StripeGateway from max_charge_retries in "
                "config/settings.yaml instead of hard-coding it."
            ),
            golden=(
                GoldenItem("config/settings.yaml", CONFIGURATION, "full"),
                GoldenItem("payments/stripe_gateway.py", SAME_CLASS_MEMBER, "full"),
            ),
            acceptable=("payments/gateway.py",),
        ),
        RecallCase(
            name="requests-upgrade",
            goal="Raise the minimum requests dependency version in pyproject.toml to 2.32.",
            golden=(GoldenItem("pyproject.toml", BUILD_METADATA, "full"),),
        ),
    ),
)

REPOSITORIES: Tuple[RecallRepository, ...] = (JAVA_SHOP, PYTHON_PAYMENTS)


def fixtures_digest(repositories: Tuple[RecallRepository, ...] = REPOSITORIES) -> str:
    """Content identity of the fixture set; part of the certification identity."""
    return hashlib.sha256(json.dumps(
        [asdict(repository) for repository in repositories], sort_keys=True,
    ).encode("utf-8")).hexdigest()


def golden_items_by_class(repositories: Tuple[RecallRepository, ...] = REPOSITORIES) -> Dict[str, int]:
    counts: Dict[str, int] = {context_class: 0 for context_class in CONTEXT_CLASSES}
    for repository in repositories:
        for case in repository.cases:
            for item in case.golden:
                counts[item.context_class] += 1
    return counts

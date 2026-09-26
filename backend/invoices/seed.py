"""
Invoice seeding module for the CRM application.

This module provides the InvoiceSeeder class which handles seeding of all
invoice-related entities: Products, InvoiceTemplates, Invoices, Payments,
Estimates, and RecurringInvoices.
"""

import datetime
import random
from decimal import Decimal

from django.utils import timezone


class InvoiceSeeder:
    """Handles seeding of all invoice-related entities."""

    # Invoice status distribution weights
    INVOICE_STATUS_WEIGHTS = {
        "Draft": 20,
        "Sent": 15,
        "Viewed": 10,
        "Paid": 25,
        "Partially_Paid": 10,
        "Overdue": 15,
        "Cancelled": 5,
    }

    # Estimate status distribution weights
    ESTIMATE_STATUS_WEIGHTS = {
        "Draft": 20,
        "Sent": 15,
        "Viewed": 15,
        "Accepted": 25,
        "Declined": 15,
        "Expired": 10,
    }

    # Payment method distribution weights
    PAYMENT_METHOD_WEIGHTS = {
        "BANK_TRANSFER": 35,
        "CREDIT_CARD": 30,
        "CHECK": 15,
        "PAYPAL": 10,
        "CASH": 5,
        "STRIPE": 4,
        "OTHER": 1,
    }

    # Payment terms distribution weights
    PAYMENT_TERMS_WEIGHTS = {
        "NET_30": 45,
        "NET_15": 20,
        "NET_60": 15,
        "DUE_ON_RECEIPT": 10,
        "NET_45": 10,
    }

    # Recurring frequency distribution weights
    RECURRING_FREQUENCY_WEIGHTS = {
        "MONTHLY": 50,
        "QUARTERLY": 20,
        "YEARLY": 15,
        "WEEKLY": 10,
        "BIWEEKLY": 5,
    }

    # Product categories
    PRODUCT_CATEGORIES = [
        "Consulting Services",
        "Software License",
        "Implementation",
        "Training",
        "Support",
        "Hardware",
        "Subscription",
        "Custom Development",
        "Integration",
        "Maintenance",
    ]

    # Product templates with realistic pricing
    PRODUCT_TEMPLATES = [
        {
            "name": "Consulting - Senior",
            "category": "Consulting Services",
            "price_range": (150, 350),
        },
        {
            "name": "Consulting - Standard",
            "category": "Consulting Services",
            "price_range": (75, 150),
        },
        {
            "name": "Implementation Services",
            "category": "Implementation",
            "price_range": (5000, 25000),
        },
        {
            "name": "Training Session (Full Day)",
            "category": "Training",
            "price_range": (1500, 3500),
        },
        {
            "name": "Training Session (Half Day)",
            "category": "Training",
            "price_range": (800, 1800),
        },
        {
            "name": "Enterprise License",
            "category": "Software License",
            "price_range": (10000, 50000),
        },
        {
            "name": "Professional License",
            "category": "Software License",
            "price_range": (1000, 5000),
        },
        {
            "name": "Team License (per seat)",
            "category": "Software License",
            "price_range": (50, 200),
        },
        {
            "name": "Annual Support Plan",
            "category": "Support",
            "price_range": (2000, 10000),
        },
        {
            "name": "Monthly Subscription",
            "category": "Subscription",
            "price_range": (99, 499),
        },
        {"name": "Premium Support", "category": "Support", "price_range": (500, 2000)},
        {
            "name": "Custom Development (per hour)",
            "category": "Custom Development",
            "price_range": (100, 250),
        },
        {
            "name": "API Integration",
            "category": "Integration",
            "price_range": (3000, 15000),
        },
        {"name": "Hardware Setup", "category": "Hardware", "price_range": (500, 2500)},
        {
            "name": "Maintenance (Monthly)",
            "category": "Maintenance",
            "price_range": (200, 1000),
        },
    ]

    # Invoice template configurations
    TEMPLATE_CONFIGS = [
        {
            "name": "Professional Blue",
            "primary_color": "#3B82F6",
            "secondary_color": "#1E40AF",
            "is_default": True,
        },
        {
            "name": "Corporate Gray",
            "primary_color": "#4B5563",
            "secondary_color": "#1F2937",
            "is_default": False,
        },
        {
            "name": "Modern Green",
            "primary_color": "#10B981",
            "secondary_color": "#047857",
            "is_default": False,
        },
        {
            "name": "Classic Red",
            "primary_color": "#EF4444",
            "secondary_color": "#B91C1C",
            "is_default": False,
        },
    ]

    def __init__(self, fake, stdout):
        """
        Initialize the InvoiceSeeder.

        Args:
            fake: Faker instance from the main command
            stdout: stdout for output messages
        """
        self.fake = fake
        self.stdout = stdout
        self.stats = {
            "products": 0,
            "invoice_templates": 0,
            "invoices": 0,
            "invoice_line_items": 0,
            "payments": 0,
            "estimates": 0,
            "estimate_line_items": 0,
            "recurring_invoices": 0,
            "recurring_invoice_line_items": 0,
        }

    def _weighted_choice(self, weights_dict):
        """Select a random key based on weights."""
        items = list(weights_dict.keys())
        weights = list(weights_dict.values())
        return random.choices(items, weights=weights, k=1)[0]

    def _generate_invoice_date(self):
        """Generate realistic invoice dates weighted toward recent months."""
        days_ago_weights = {
            (0, 30): 35,  # Last month: 35%
            (31, 60): 25,  # 1-2 months ago: 25%
            (61, 90): 20,  # 2-3 months ago: 20%
            (91, 120): 10,  # 3-4 months ago: 10%
            (121, 150): 6,  # 4-5 months ago: 6%
            (151, 180): 4,  # 5-6 months ago: 4%
        }
        range_choice = self._weighted_choice(days_ago_weights)
        days_ago = random.randint(range_choice[0], range_choice[1])
        return timezone.localdate() - datetime.timedelta(days=days_ago)

    def _add_invoice_assignments(self, instance, profiles, teams):
        """Add M2M assignments to invoice-related entities (no tags)."""
        if profiles:
            num_assigned = random.randint(1, min(2, len(profiles)))
            instance.assigned_to.add(*random.sample(profiles, num_assigned))

        if teams and random.random() > 0.3:
            instance.teams.add(random.choice(teams))

    def create_products(self, org, count):
        """
        Create product catalog items for the organization.

        Args:
            org: Organization instance
            count: Number of products to create

        Returns:
            List of created Product instances
        """
        from invoices.models import Product

        products = []

        # Use product templates first
        templates_to_use = random.sample(
            self.PRODUCT_TEMPLATES, min(count, len(self.PRODUCT_TEMPLATES))
        )

        for i, template in enumerate(templates_to_use):
            product = Product.objects.create(
                name=template["name"],
                description=self.fake.paragraph(nb_sentences=2),
                sku=f"SKU-{org.id.hex[:4].upper()}-{i + 1:04d}",
                price=Decimal(str(random.randint(*template["price_range"]))),
                currency=org.default_currency,
                category=template["category"],
                is_active=True,
                org=org,
            )
            products.append(product)
            self.stats["products"] += 1

        # Add extra generic products if needed
        remaining = count - len(templates_to_use)
        for i in range(remaining):
            category = random.choice(self.PRODUCT_CATEGORIES)
            product = Product.objects.create(
                name=f"{category} - {self.fake.word().title()}",
                description=self.fake.paragraph(nb_sentences=2),
                sku=f"SKU-{org.id.hex[:4].upper()}-{len(templates_to_use) + i + 1:04d}",
                price=Decimal(str(random.randint(100, 10000))),
                currency=org.default_currency,
                category=category,
                is_active=random.random() > 0.1,  # 90% active
                org=org,
            )
            products.append(product)
            self.stats["products"] += 1

        self.stdout.write(f"  Created {len(products)} products")
        return products

    def create_invoice_templates(self, org, count):
        """
        Create invoice PDF templates for the organization.

        Args:
            org: Organization instance
            count: Number of templates to create

        Returns:
            List of created InvoiceTemplate instances
        """
        from invoices.models import InvoiceTemplate

        templates = []
        configs_to_use = self.TEMPLATE_CONFIGS[: min(count, len(self.TEMPLATE_CONFIGS))]

        for config in configs_to_use:
            template = InvoiceTemplate.objects.create(
                name=config["name"],
                primary_color=config["primary_color"],
                secondary_color=config["secondary_color"],
                default_notes="Thank you for your business!",
                default_terms="Payment is due within the specified terms. Late payments may incur additional fees.",
                footer_text=f"Questions? Contact us at {org.email if hasattr(org, 'email') and org.email else 'billing@company.com'}",
                is_default=config["is_default"],
                org=org,
            )
            templates.append(template)
            self.stats["invoice_templates"] += 1

        self.stdout.write(f"  Created {len(templates)} invoice templates")
        return templates

    def create_invoices(
        self,
        org,
        profiles,
        teams,
        products,
        templates,
        accounts,
        contacts,
        opportunities,
        count,
    ):
        """
        Create invoices with line items.

        Args:
            org: Organization instance
            profiles: List of Profile instances
            teams: List of Teams instances
            products: List of Product instances
            templates: List of InvoiceTemplate instances
            accounts: List of Account instances
            contacts: List of Contact instances
            opportunities: List of Opportunity instances
            count: Number of invoices to create

        Returns:
            List of created Invoice instances
        """
        from invoices.models import Invoice, InvoiceLineItem
        from opportunity.stages import stage_index
        from opportunity.workflow import WON

        invoices = []

        # Won opportunities for linking: the stage's kind, whatever its name.
        stages = stage_index(org.id)
        won_opps = [
            o
            for o in opportunities
            if (stage := o.current_stage(stages)) is not None and stage.kind == WON
        ]

        for i in range(count):
            # Generate issue date (weighted toward recent)
            issue_date = self._generate_invoice_date()
            date_str = issue_date.strftime("%Y%m%d")

            # Select account and contact
            account = random.choice(accounts)
            # Prefer contacts linked to this account
            account_contacts = (
                list(account.contacts.all()) if hasattr(account, "contacts") else []
            )
            contact = (
                random.choice(account_contacts)
                if account_contacts
                else random.choice(contacts)
            )

            # Determine status
            status = self._weighted_choice(self.INVOICE_STATUS_WEIGHTS)

            # Generate unique invoice number
            invoice_number = f"INV-{date_str}-{i + 1:04d}"

            # Optionally link to opportunity (~30% of invoices)
            opportunity = None
            if won_opps and random.random() < 0.3:
                # Try to find an opp for this account
                account_opps = [o for o in won_opps if o.account_id == account.id]
                opportunity = (
                    random.choice(account_opps)
                    if account_opps
                    else random.choice(won_opps)
                )

            payment_terms = self._weighted_choice(self.PAYMENT_TERMS_WEIGHTS)
            tax_rate = Decimal(str(random.choice([0, 7.5, 8.25, 10, 12, 20])))

            invoice = Invoice(
                invoice_title=f"Invoice for {account.name}",
                invoice_number=invoice_number,
                status=status,
                account=account,
                contact=contact,
                opportunity=opportunity,
                # Denormalized client fields
                client_name=f"{contact.first_name} {contact.last_name}",
                client_email=contact.email or "",
                client_phone=contact.phone or "",
                client_address_line=contact.address_line or "",
                client_city=contact.city or "",
                client_state=contact.state or "",
                client_postcode=contact.postcode or "",
                client_country=contact.country or "",
                # Billing from org (could be from org company profile)
                billing_address_line=getattr(org, "address_line", "") or "",
                billing_city=getattr(org, "city", "") or "",
                billing_state=getattr(org, "state", "") or "",
                billing_postcode=getattr(org, "postcode", "") or "",
                billing_country=getattr(org, "billing_country", "") or "",
                # Financial
                currency=org.default_currency,
                discount_type=random.choice(["PERCENTAGE", "FIXED", ""])
                if random.random() > 0.6
                else "",
                discount_value=Decimal(str(random.choice([0, 5, 10, 15])))
                if random.random() > 0.6
                else Decimal("0"),
                tax_rate=tax_rate,
                # Dates
                issue_date=issue_date,
                payment_terms=payment_terms,
                # Template
                template=random.choice(templates) if templates else None,
                notes=self.fake.paragraph() if random.random() > 0.5 else "",
                terms="Payment is due within the specified terms.",
                org=org,
            )

            # Handle status-specific timestamps
            if status in ["Sent", "Viewed", "Paid", "Partially_Paid", "Overdue"]:
                invoice.sent_at = timezone.make_aware(
                    datetime.datetime.combine(issue_date, datetime.time(10, 0))
                )
            if status in ["Viewed", "Paid", "Partially_Paid", "Overdue"]:
                invoice.viewed_at = invoice.sent_at + datetime.timedelta(
                    days=random.randint(1, 5)
                )
            if status == "Paid":
                invoice.paid_at = invoice.viewed_at + datetime.timedelta(
                    days=random.randint(1, 15)
                )

            # Save invoice (this auto-generates public_token and calculates due_date)
            invoice.save()

            # Create 2-5 line items
            num_items = random.randint(2, 5)
            for order in range(num_items):
                product = (
                    random.choice(products)
                    if products and random.random() > 0.3
                    else None
                )

                if product:
                    name = product.name
                    unit_price = product.price
                    description = product.description or ""
                else:
                    name = self.fake.bs().title()[:100]
                    unit_price = Decimal(str(random.randint(100, 5000)))
                    description = self.fake.sentence()

                InvoiceLineItem.objects.create(
                    invoice=invoice,
                    product=product,
                    name=name,
                    description=description[:500],
                    quantity=Decimal(str(random.choice([1, 1, 1, 2, 3, 5, 10]))),
                    unit_price=unit_price,
                    discount_type=""
                    if random.random() > 0.2
                    else random.choice(["PERCENTAGE", "FIXED"]),
                    discount_value=Decimal("0"),
                    tax_rate=tax_rate if random.random() > 0.3 else Decimal("0"),
                    order=order,
                    org=org,
                )
                self.stats["invoice_line_items"] += 1

            # Recalculate totals after line items
            invoice.recalculate_totals()
            invoice.save()

            # Add M2M assignments
            self._add_invoice_assignments(invoice, profiles, teams)

            invoices.append(invoice)
            self.stats["invoices"] += 1

        self.stdout.write(f"  Created {len(invoices)} invoices with line items")
        return invoices

    def create_payments(self, org, invoices):
        """
        Create payments for invoices.

        Args:
            org: Organization instance
            invoices: List of Invoice instances

        Returns:
            List of created Payment instances
        """
        from invoices.models import Payment

        payments = []

        # Only create payments for certain invoice statuses
        payable_invoices = [
            inv
            for inv in invoices
            if inv.status in ["Paid", "Partially_Paid", "Sent", "Viewed", "Overdue"]
        ]

        for invoice in payable_invoices:
            if invoice.status == "Paid":
                # Full payment
                payment_date = (
                    timezone.localdate(invoice.paid_at)
                    if invoice.paid_at
                    else (
                        invoice.issue_date
                        + datetime.timedelta(days=random.randint(5, 30))
                    )
                )
                payment = Payment.objects.create(
                    invoice=invoice,
                    amount=invoice.total_amount,
                    payment_date=payment_date,
                    payment_method=self._weighted_choice(self.PAYMENT_METHOD_WEIGHTS),
                    reference_number=f"PAY-{self.fake.bothify('??###??').upper()}",
                    notes="" if random.random() > 0.3 else self.fake.sentence(),
                    org=org,
                )
                payments.append(payment)
                self.stats["payments"] += 1

            elif invoice.status == "Partially_Paid":
                # Partial payment (30-70% of total)
                partial_percent = random.uniform(0.3, 0.7)
                payment_amount = invoice.total_amount * Decimal(str(partial_percent))
                payment_date = invoice.issue_date + datetime.timedelta(
                    days=random.randint(5, 20)
                )

                payment = Payment.objects.create(
                    invoice=invoice,
                    amount=payment_amount.quantize(Decimal("0.01")),
                    payment_date=payment_date,
                    payment_method=self._weighted_choice(self.PAYMENT_METHOD_WEIGHTS),
                    reference_number=f"PAY-{self.fake.bothify('??###??').upper()}",
                    notes="Partial payment",
                    org=org,
                )
                payments.append(payment)
                self.stats["payments"] += 1

            elif random.random() < 0.3:  # 30% chance of payment on other statuses
                # Some random payments
                payment_amount = invoice.total_amount * Decimal(
                    str(random.uniform(0.2, 0.5))
                )
                payment_date = invoice.issue_date + datetime.timedelta(
                    days=random.randint(10, 45)
                )

                payment = Payment.objects.create(
                    invoice=invoice,
                    amount=payment_amount.quantize(Decimal("0.01")),
                    payment_date=min(payment_date, timezone.localdate()),
                    payment_method=self._weighted_choice(self.PAYMENT_METHOD_WEIGHTS),
                    reference_number=f"PAY-{self.fake.bothify('??###??').upper()}",
                    notes="",
                    org=org,
                )
                payments.append(payment)
                self.stats["payments"] += 1

        self.stdout.write(f"  Created {len(payments)} payments")
        return payments

    def create_estimates(
        self, org, profiles, teams, products, accounts, contacts, count
    ):
        """
        Create estimates with line items.

        Args:
            org: Organization instance
            profiles: List of Profile instances
            teams: List of Teams instances
            products: List of Product instances
            accounts: List of Account instances
            contacts: List of Contact instances
            count: Number of estimates to create

        Returns:
            List of created Estimate instances
        """
        from invoices.models import Estimate, EstimateLineItem

        estimates = []

        for i in range(count):
            issue_date = self._generate_invoice_date()
            date_str = issue_date.strftime("%Y%m%d")

            account = random.choice(accounts)
            account_contacts = (
                list(account.contacts.all()) if hasattr(account, "contacts") else []
            )
            contact = (
                random.choice(account_contacts)
                if account_contacts
                else random.choice(contacts)
            )

            status = self._weighted_choice(self.ESTIMATE_STATUS_WEIGHTS)

            # Calculate expiry (30-90 days from issue)
            expiry_date = issue_date + datetime.timedelta(days=random.randint(30, 90))

            tax_rate = Decimal(str(random.choice([0, 7.5, 8.25, 10])))

            estimate = Estimate(
                estimate_number=f"EST-{date_str}-{i + 1:04d}",
                title=f"Estimate for {account.name}",
                status=status,
                account=account,
                contact=contact,
                client_name=f"{contact.first_name} {contact.last_name}",
                client_email=contact.email or "",
                client_phone=contact.phone or "",
                client_address_line=contact.address_line or "",
                client_city=contact.city or "",
                client_state=contact.state or "",
                client_postcode=contact.postcode or "",
                client_country=contact.country or "",
                currency=org.default_currency,
                discount_type=random.choice(["PERCENTAGE", "FIXED", ""])
                if random.random() > 0.7
                else "",
                discount_value=Decimal("0"),
                tax_rate=tax_rate,
                issue_date=issue_date,
                expiry_date=expiry_date,
                notes=self.fake.paragraph() if random.random() > 0.5 else "",
                terms="This estimate is valid for 30 days.",
                org=org,
            )

            # Status timestamps
            if status in ["Sent", "Viewed", "Accepted", "Declined"]:
                estimate.sent_at = timezone.make_aware(
                    datetime.datetime.combine(issue_date, datetime.time(10, 0))
                )
            if status in ["Viewed", "Accepted", "Declined"]:
                estimate.viewed_at = estimate.sent_at + datetime.timedelta(
                    days=random.randint(1, 5)
                )
            if status == "Accepted":
                estimate.accepted_at = estimate.viewed_at + datetime.timedelta(
                    days=random.randint(1, 10)
                )
            if status == "Declined":
                estimate.declined_at = estimate.viewed_at + datetime.timedelta(
                    days=random.randint(1, 10)
                )

            estimate.save()

            # Create 2-4 line items
            num_items = random.randint(2, 4)
            for order in range(num_items):
                product = (
                    random.choice(products)
                    if products and random.random() > 0.3
                    else None
                )

                if product:
                    name = product.name
                    unit_price = product.price
                    description = product.description or ""
                else:
                    name = self.fake.bs().title()[:100]
                    unit_price = Decimal(str(random.randint(500, 10000)))
                    description = self.fake.sentence()

                EstimateLineItem.objects.create(
                    estimate=estimate,
                    product=product,
                    name=name,
                    description=description[:500],
                    quantity=Decimal(str(random.choice([1, 1, 2, 5]))),
                    unit_price=unit_price,
                    tax_rate=tax_rate,
                    order=order,
                    org=org,
                )
                self.stats["estimate_line_items"] += 1

            estimate.recalculate_totals()
            estimate.save()

            self._add_invoice_assignments(estimate, profiles, teams)

            estimates.append(estimate)
            self.stats["estimates"] += 1

        self.stdout.write(f"  Created {len(estimates)} estimates with line items")
        return estimates

    def create_recurring_invoices(
        self, org, profiles, teams, products, accounts, contacts, count
    ):
        """
        Create recurring invoice templates.

        Args:
            org: Organization instance
            profiles: List of Profile instances
            teams: List of Teams instances
            products: List of Product instances
            accounts: List of Account instances
            contacts: List of Contact instances
            count: Number of recurring invoices to create

        Returns:
            List of created RecurringInvoice instances
        """
        from invoices.models import RecurringInvoice, RecurringInvoiceLineItem

        recurring = []

        for _ in range(count):
            account = random.choice(accounts)
            account_contacts = (
                list(account.contacts.all()) if hasattr(account, "contacts") else []
            )
            contact = (
                random.choice(account_contacts)
                if account_contacts
                else random.choice(contacts)
            )

            frequency = self._weighted_choice(self.RECURRING_FREQUENCY_WEIGHTS)
            start_date = self.fake.date_between(start_date="-60d", end_date="+30d")

            # Some recurring invoices have end dates
            end_date = None
            if random.random() > 0.6:
                end_date = start_date + datetime.timedelta(
                    days=random.randint(180, 730)
                )

            tax_rate = Decimal(str(random.choice([0, 8, 10, 12])))

            rec = RecurringInvoice(
                title=f"Recurring - {account.name} ({frequency.replace('_', ' ').title()})",
                is_active=random.random() > 0.15,  # 85% active
                account=account,
                contact=contact,
                client_name=f"{contact.first_name} {contact.last_name}",
                client_email=contact.email or "",
                frequency=frequency,
                custom_days=random.randint(7, 90) if frequency == "CUSTOM" else None,
                start_date=start_date,
                end_date=end_date,
                next_generation_date=max(start_date, timezone.localdate()),
                payment_terms=self._weighted_choice(self.PAYMENT_TERMS_WEIGHTS),
                auto_send=random.random() > 0.5,
                currency=org.default_currency,
                discount_type=random.choice(["", "PERCENTAGE"]),
                discount_value=Decimal("0"),
                tax_rate=tax_rate,
                notes="Auto-generated recurring invoice",
                invoices_generated=random.randint(0, 12)
                if start_date < timezone.localdate()
                else 0,
                org=org,
            )
            rec.save()

            # Create 1-3 line items
            num_items = random.randint(1, 3)
            for order in range(num_items):
                product = random.choice(products) if products else None

                if product:
                    name = product.name
                    unit_price = product.price
                    description = product.description or ""
                else:
                    name = f"Monthly {self.fake.word().title()} Service"
                    unit_price = Decimal(str(random.randint(99, 999)))
                    description = "Recurring service"

                RecurringInvoiceLineItem.objects.create(
                    recurring_invoice=rec,
                    product=product,
                    name=name,
                    description=description[:500],
                    quantity=Decimal("1"),
                    unit_price=unit_price,
                    tax_rate=tax_rate,
                    order=order,
                    org=org,
                )
                self.stats["recurring_invoice_line_items"] += 1

            self._add_invoice_assignments(rec, profiles, teams)

            recurring.append(rec)
            self.stats["recurring_invoices"] += 1

        self.stdout.write(f"  Created {len(recurring)} recurring invoices")
        return recurring

    def clear_invoice_data(self):
        """Clear all invoice-related data in reverse dependency order."""
        from invoices.models import (
            Estimate,
            EstimateLineItem,
            Invoice,
            InvoiceHistory,
            InvoiceLineItem,
            InvoiceTemplate,
            Payment,
            Product,
            RecurringInvoice,
            RecurringInvoiceLineItem,
        )

        # Delete in reverse dependency order
        Payment.objects.all().delete()
        InvoiceHistory.objects.all().delete()
        InvoiceLineItem.objects.all().delete()
        Invoice.objects.all().delete()
        EstimateLineItem.objects.all().delete()
        Estimate.objects.all().delete()
        RecurringInvoiceLineItem.objects.all().delete()
        RecurringInvoice.objects.all().delete()
        Product.objects.all().delete()
        InvoiceTemplate.objects.all().delete()

        self.stdout.write(
            "  Cleared: Payments, InvoiceHistory, InvoiceLineItems, Invoices, "
            "EstimateLineItems, Estimates, RecurringInvoiceLineItems, RecurringInvoices, "
            "Products, InvoiceTemplates"
        )

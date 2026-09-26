"""
Management command to seed test data for the CRM application.

All identifying values (person names, company names, emails, websites, phone
numbers, street addresses) are drawn from curated fictional pools and use
IANA-reserved demo domains (`example.com` / `.example`) and the NANP fictional
phone range (`+1 555-555-01XX`). Seeded data is safe to show in demos,
marketing screenshots, and recorded walk-throughs without risk of resembling
a real person or business.

Usage:
    python manage.py seed_data --email admin@example.com
    python manage.py seed_data --email admin@example.com --orgs 2 --leads 100 --seed 42
    python manage.py seed_data --email admin@example.com --currency EUR --country DE
    python manage.py seed_data --email admin@example.com --clear --no-input
"""

import random
from decimal import Decimal

from crum import impersonate
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from faker import Faker

from common.models import Org, Profile, Tags, Teams, User
from common.rls import get_set_context_sql
from common.utils import (
    CASE_TYPE,
    CURRENCY_CODES,
    INDCHOICES,
    LEAD_SOURCE,
    OPPORTUNITY_TYPES,
    PRIORITY_CHOICE,
    SOURCES,
)
from invoices.seed import InvoiceSeeder


class Command(BaseCommand):
    help = "Seed the database with realistic test CRM data"

    # Status distribution weights (simulate realistic data)
    LEAD_STATUS_WEIGHTS = {
        "assigned": 40,
        "in process": 30,
        "converted": 15,
        "recycled": 10,
        "closed": 5,
    }
    OPP_STAGE_WEIGHTS = {
        "PROSPECTING": 15,
        "QUALIFICATION": 15,
        "PROPOSAL": 15,
        "NEGOTIATION": 15,
        "CLOSED_WON": 25,
        "CLOSED_LOST": 15,
    }
    CASE_STATUS_WEIGHTS = {
        "New": 40,
        "Assigned": 25,
        "Pending": 20,
        "Closed": 10,
        "Rejected": 5,
    }
    TASK_STATUS_WEIGHTS = {
        "New": 30,
        "In Progress": 40,
        "Completed": 30,
    }

    # Common tag names for CRM
    TAG_NAMES = [
        "Hot Lead",
        "Enterprise",
        "SMB",
        "Renewal",
        "Upsell",
        "High Priority",
        "Follow Up",
        "Decision Maker",
        "Influencer",
        "Champion",
        "At Risk",
        "New Customer",
        "VIP",
        "Partner Referral",
        "Inbound",
    ]

    # Country code to Faker locale mapping
    COUNTRY_FAKER_LOCALES = {
        "US": "en_US",
        "GB": "en_GB",
        "CA": "en_CA",
        "AU": "en_AU",
        "DE": "de_DE",
        "FR": "fr_FR",
        "IN": "en_IN",
        "JP": "ja_JP",
        "CN": "zh_CN",
        "BR": "pt_BR",
        "MX": "es_MX",
        "SG": "en_SG",
        "AE": "ar_AE",
        "CH": "de_CH",
    }

    # Team name templates
    TEAM_NAMES = [
        ("Sales Team", "Primary sales team handling all inbound leads"),
        ("Enterprise Sales", "Dedicated team for enterprise accounts"),
        ("SMB Team", "Small and medium business focused team"),
        ("Customer Success", "Post-sales customer success team"),
        ("Support Team", "Technical support and case management"),
        ("Marketing Ops", "Marketing operations and campaign management"),
    ]

    # Curated demo person first names. Common given names spanning multiple
    # regions; deliberately exclude well-known public figures so a chance
    # pairing in seed output never reads as a real notable person.
    DEMO_FIRST_NAMES = [
        "Aaron",
        "Alex",
        "Amelia",
        "Anya",
        "Avery",
        "Beatrice",
        "Cameron",
        "Carlos",
        "Casey",
        "Chloe",
        "Daniel",
        "Dara",
        "Diana",
        "Eden",
        "Eleanor",
        "Elena",
        "Felix",
        "Gabriel",
        "Gabriella",
        "Grace",
        "Hannah",
        "Henry",
        "Ines",
        "Iris",
        "Ivan",
        "Jamie",
        "Jordan",
        "Julia",
        "Kai",
        "Kira",
        "Lara",
        "Liam",
        "Maya",
        "Miguel",
        "Mira",
        "Nadia",
        "Nina",
        "Noah",
        "Olivia",
        "Owen",
        "Patricia",
        "Priya",
        "Quinn",
        "Rachel",
        "Ravi",
        "Riley",
        "Rosa",
        "Samira",
        "Sebastian",
        "Sofia",
        "Theo",
        "Uma",
        "Victor",
        "Vivian",
        "Wesley",
        "Yara",
        "Zoe",
    ]

    # Curated demo person last names. Common surnames across multiple
    # cultures; deliberately exclude major political and celebrity surnames.
    DEMO_LAST_NAMES = [
        "Anderson",
        "Bennett",
        "Brooks",
        "Carter",
        "Chen",
        "Cohen",
        "Davis",
        "Diaz",
        "Edwards",
        "Foster",
        "Garcia",
        "Gupta",
        "Harper",
        "Hughes",
        "Iyer",
        "James",
        "Jensen",
        "Kim",
        "Larsen",
        "Lewis",
        "Martin",
        "Mitchell",
        "Nakamura",
        "Nguyen",
        "Okafor",
        "Patel",
        "Phillips",
        "Reyes",
        "Rivera",
        "Rodriguez",
        "Russell",
        "Sato",
        "Singh",
        "Stewart",
        "Tan",
        "Tanaka",
        "Thompson",
        "Turner",
        "Walker",
        "Wright",
    ]

    # Fictional company names. Hand-crafted compound names that read as
    # plausible businesses but match no real-world company; spread across
    # industries (software, finance, retail, healthcare, energy, services)
    # so seeded accounts and leads look varied.
    DEMO_COMPANIES = [
        "Lumen Stack",
        "Nebula Compute",
        "Brightforge Labs",
        "Cardinal Software",
        "Beacon Systems",
        "Drift Cartography",
        "Northwind Cloud",
        "Stonehill Analytics",
        "Verdant Logic",
        "Quill & Cipher Consulting",
        "Inkwell Studios",
        "Highwater Capital",
        "Foundry Financial",
        "Quayside Holdings",
        "Meridian Equity",
        "Slate & Pine Advisors",
        "Citrine Brokerage",
        "Bridgepoint Finance",
        "Mosswood Accountancy",
        "Sundial Manufacturing",
        "Ironvale Forge",
        "Pinecrest Materials",
        "Granite Peak Industries",
        "Auriga Engineering",
        "Helix Hardware",
        "Ridgeline Mechanical",
        "Driftwood Apparel",
        "Tidepool Cosmetics",
        "Sumac Outfitters",
        "Linden & Co.",
        "Foxglove Home",
        "Reedmark Furniture",
        "Aster Health",
        "Pinegrove Clinic Group",
        "Solace Therapeutics",
        "Veridian Bioworks",
        "Compass Wellness",
        "Belltower Logistics",
        "Driftway Couriers",
        "Cobblestone Hospitality",
        "Lakebridge Hotels",
        "Greenline Travel",
        "Solis Renewables",
        "Hightide Energy",
        "Mosaic Solar",
        "Birchgrove Utilities",
        "Currentline Power",
        "Tessellate Design",
        "Brackish Marketing",
        "Plumeline Media",
        "Sapling Academy",
        "Echelon Learning",
        "Lighthouse Foundation",
    ]

    # Fictional street names (paired with a Faker-generated city/state and a
    # random number, so the resulting address can't land on a real residence).
    DEMO_STREET_NAMES = [
        "Maple Avenue",
        "Cedar Lane",
        "Elm Street",
        "Sycamore Drive",
        "Birchwood Court",
        "Aspen Way",
        "Linden Road",
        "Willow Bend",
        "Magnolia Place",
        "Juniper Trail",
        "Hawthorn Circle",
        "Chestnut Boulevard",
        "Sequoia Drive",
        "Tamarack Pass",
        "Cypress Court",
        "Olive Branch Lane",
        "Beech Tree Road",
        "Hemlock Hollow",
        "Larkspur Lane",
        "Riverbend Drive",
    ]

    # Common job titles for seeded contacts / leads.
    DEMO_JOB_TITLES = [
        "Account Manager",
        "Operations Director",
        "Sales Representative",
        "Product Manager",
        "Engineering Lead",
        "Marketing Specialist",
        "Customer Success Manager",
        "Chief Technology Officer",
        "Chief Executive Officer",
        "Chief Financial Officer",
        "Vice President of Sales",
        "VP of Operations",
        "Director of Marketing",
        "Director of Engineering",
        "Senior Software Engineer",
        "Software Engineer",
        "Solutions Architect",
        "Data Scientist",
        "Business Analyst",
        "Project Manager",
        "Procurement Manager",
        "HR Manager",
        "Office Administrator",
        "Field Sales Manager",
        "Regional Director",
        "Inside Sales Representative",
        "Customer Support Lead",
        "Implementation Specialist",
        "Partner Manager",
        "Channel Manager",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fake = None
        self.admin_user = None
        self.invoice_seeder = None
        self.stats = {
            "orgs": 0,
            "users": 0,
            "profiles": 0,
            "teams": 0,
            "tags": 0,
            "contacts": 0,
            "accounts": 0,
            "leads": 0,
            "opportunities": 0,
            "cases": 0,
            "tasks": 0,
            "goals": 0,
        }

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            default="aswin.1231@gmail.com",
            help="Admin user email (default: aswin.1231@gmail.com)",
        )

        # Entity counts
        parser.add_argument(
            "--orgs",
            type=int,
            default=1,
            help="Number of organizations to create (default: 1)",
        )
        parser.add_argument(
            "--users-per-org",
            type=int,
            default=3,
            help="Additional users per organization (default: 3)",
        )
        parser.add_argument(
            "--leads",
            type=int,
            default=20,
            help="Leads per organization (default: 20)",
        )
        parser.add_argument(
            "--accounts",
            type=int,
            default=10,
            help="Accounts per organization (default: 10)",
        )
        parser.add_argument(
            "--contacts",
            type=int,
            default=15,
            help="Contacts per organization (default: 15)",
        )
        parser.add_argument(
            "--opportunities",
            type=int,
            default=10,
            help="Opportunities per organization (default: 10)",
        )
        parser.add_argument(
            "--cases",
            type=int,
            default=5,
            help="Cases per organization (default: 5)",
        )
        parser.add_argument(
            "--tasks",
            type=int,
            default=10,
            help="Tasks per organization (default: 10)",
        )
        parser.add_argument(
            "--goals",
            type=int,
            default=8,
            help="Sales goals per organization (default: 8)",
        )
        parser.add_argument(
            "--teams",
            type=int,
            default=2,
            help="Teams per organization (default: 2)",
        )
        parser.add_argument(
            "--tags",
            type=int,
            default=5,
            help="Tags per organization (default: 5)",
        )

        # Locale options
        valid_currencies = [c[0] for c in CURRENCY_CODES]
        parser.add_argument(
            "--currency",
            type=str,
            default="USD",
            choices=valid_currencies,
            help=f"Default currency for organizations (default: USD). Choices: {', '.join(valid_currencies)}",
        )
        parser.add_argument(
            "--country",
            type=str,
            default="US",
            help="Default country code for organizations (default: US). Examples: US, GB, CA, AU, DE, FR, IN",
        )

        # Invoice-related arguments
        parser.add_argument(
            "--products",
            type=int,
            default=20,
            help="Products per organization (default: 20)",
        )
        parser.add_argument(
            "--invoices",
            type=int,
            default=50,
            help="Invoices per organization (default: 50)",
        )
        parser.add_argument(
            "--estimates",
            type=int,
            default=15,
            help="Estimates per organization (default: 15)",
        )
        parser.add_argument(
            "--recurring-invoices",
            type=int,
            default=5,
            help="Recurring invoices per organization (default: 5)",
        )
        parser.add_argument(
            "--invoice-templates",
            type=int,
            default=3,
            help="Invoice templates per organization (default: 3)",
        )

        # Options
        parser.add_argument(
            "--seed",
            type=int,
            help="Random seed for reproducibility",
        )
        parser.add_argument(
            "--password",
            type=str,
            default="testpass123",
            help="Password for new users (default: testpass123)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing CRM data before seeding",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Skip confirmation prompts",
        )

    def handle(self, *args, **options):
        # Initialize Faker with locale matching the country
        country = options["country"].upper()
        locale = self.COUNTRY_FAKER_LOCALES.get(country, "en_US")

        seed = options.get("seed")
        if seed:
            random.seed(seed)
            self.fake = Faker([locale])
            Faker.seed(seed)
        else:
            self.fake = Faker([locale])

        # Initialize InvoiceSeeder
        self.invoice_seeder = InvoiceSeeder(self.fake, self.stdout)

        self.stdout.write(self.style.MIGRATE_HEADING("Seeding CRM database..."))
        self.stdout.write(
            f"Currency: {options['currency']}, Country: {country}, Locale: {locale}"
        )
        if seed:
            self.stdout.write(f"Using seed: {seed}")

        # Handle clear option
        if options["clear"]:
            self.handle_clear(options)

        # Get or create admin user
        self.admin_user = self.get_or_create_admin(
            options["email"], options["password"]
        )

        start_time = timezone.now()

        try:
            with transaction.atomic():
                with impersonate(self.admin_user):
                    self.seed_all(options)
        except Exception as e:
            raise CommandError(f"Seeding failed: {e}") from e

        elapsed = (timezone.now() - start_time).total_seconds()
        self.print_summary(elapsed)

    def handle_clear(self, options):
        """Handle the --clear option."""
        if not options["no_input"]:
            confirm = input(
                "This will delete existing CRM data (not users/orgs). Continue? [y/N]: "
            )
            if confirm.lower() != "y":
                raise CommandError("Operation cancelled.")

        self.stdout.write("Clearing existing CRM data...")
        self.clear_data()
        self.stdout.write(self.style.SUCCESS("Data cleared."))

    def clear_data(self):
        """Clear CRM data in reverse dependency order (preserves Users/Orgs/Profiles)."""
        from accounts.models import Account
        from cases.models import Case
        from contacts.models import Contact
        from leads.models import Lead
        from opportunity.models import Opportunity, SalesGoal
        from tasks.models import Task

        # Clear invoice data first (depends on accounts/contacts)
        self.invoice_seeder.clear_invoice_data()

        # Delete in reverse dependency order
        Task.objects.all().delete()
        Case.objects.all().delete()
        SalesGoal.objects.all().delete()
        Opportunity.objects.all().delete()
        Lead.objects.all().delete()
        Account.objects.all().delete()
        Contact.objects.all().delete()
        Teams.objects.all().delete()
        Tags.objects.all().delete()

        self.stdout.write(
            "  Cleared: Tasks, Cases, Sales Goals, Opportunities, Leads, Accounts, Contacts, Teams, Tags"
        )

    def get_or_create_admin(self, email, password):
        """Get existing user or create new admin user."""
        try:
            user = User.objects.get(email=email)
            self.stdout.write(f"Using existing user: {email}")
        except User.DoesNotExist:
            user = User.objects.create_user(email=email, password=password)
            self.stdout.write(self.style.SUCCESS(f"Created user: {email}"))
            self.stats["users"] += 1
        return user

    def set_rls_context(self, org_id):
        """Set the RLS context for database operations."""
        with connection.cursor() as cursor:
            cursor.execute(get_set_context_sql(), [str(org_id)])

    def seed_all(self, options):
        """Main seeding orchestration."""
        for i in range(options["orgs"]):
            self.stdout.write(f"\n--- Organization {i + 1}/{options['orgs']} ---")
            org = self.create_org(options["currency"], options["country"], i)
            # Set RLS context for this org before creating org-scoped data
            self.set_rls_context(org.id)
            profiles = self.create_profiles(
                org, options["users_per_org"], options["password"]
            )
            teams = self.create_teams(org, profiles, options["teams"])
            tags = self.create_tags(org, options["tags"])
            contacts = self.create_contacts(
                org, profiles, teams, tags, options["contacts"]
            )
            accounts = self.create_accounts(
                org, profiles, teams, tags, contacts, options["accounts"]
            )

            # Invoice prerequisites
            products = self.invoice_seeder.create_products(org, options["products"])
            templates = self.invoice_seeder.create_invoice_templates(
                org, options["invoice_templates"]
            )

            leads = self.create_leads(
                org, profiles, teams, tags, contacts, options["leads"]
            )
            opportunities = self.create_opportunities(
                org, profiles, teams, tags, contacts, accounts, options["opportunities"]
            )

            # Invoice entities
            invoices = self.invoice_seeder.create_invoices(
                org,
                profiles,
                teams,
                products,
                templates,
                accounts,
                contacts,
                opportunities,
                options["invoices"],
            )
            self.invoice_seeder.create_payments(org, invoices)
            self.invoice_seeder.create_estimates(
                org, profiles, teams, products, accounts, contacts, options["estimates"]
            )
            self.invoice_seeder.create_recurring_invoices(
                org,
                profiles,
                teams,
                products,
                accounts,
                contacts,
                options["recurring_invoices"],
            )

            cases = self.create_cases(
                org, profiles, teams, tags, contacts, accounts, options["cases"]
            )
            self.create_sales_goals(org, profiles, teams, options["goals"])
            self.create_tasks(
                org,
                profiles,
                teams,
                tags,
                contacts,
                accounts,
                leads,
                opportunities,
                cases,
                options["tasks"],
            )

    def create_org(self, currency, country, index=0):
        """Get or create an organization. The first org is always 'MicroPyramid' so
        local-dev workflows have a known name to log into via `manage.py devlogin`.

        Uses get_or_create on name so repeated seed runs reuse the same org rather
        than piling up duplicates. Duplicate names break `devlogin --org NAME`
        because it can't disambiguate.
        """
        name = "MicroPyramid" if index == 0 else self._demo_company()
        org, created = Org.objects.get_or_create(
            name=name,
            defaults={
                "default_currency": currency,
                "default_country": country,
            },
        )
        if created:
            self.stats["orgs"] += 1
            self.stdout.write(
                f"  Created org: {org.name} ({org.default_currency}, {org.default_country})"
            )
        else:
            self.stdout.write(
                f"  Reusing existing org: {org.name} ({org.default_currency}, {org.default_country})"
            )
        return org

    def create_profiles(self, org, user_count, password):
        """Create profiles for admin user and additional users."""
        profiles = []

        # Create admin profile in this org
        admin_profile, created = Profile.objects.get_or_create(
            user=self.admin_user,
            org=org,
            defaults={
                "role": "ADMIN",
                "has_sales_access": True,
                "has_marketing_access": True,
                "is_active": True,
            },
        )
        profiles.append(admin_profile)
        if created:
            self.stats["profiles"] += 1

        # Create additional users for this org
        for i in range(user_count):
            email = f"user{i + 1}_{org.id.hex[:8]}@example.com"
            user, user_created = User.objects.get_or_create(
                email=email,
                defaults={"is_active": True},
            )
            if user_created:
                user.set_password(password)
                user.save()
                self.stats["users"] += 1

            profile, profile_created = Profile.objects.get_or_create(
                user=user,
                org=org,
                defaults={
                    "role": "USER",
                    "has_sales_access": random.choice([True, False]),
                    "has_marketing_access": random.choice([True, False]),
                    "is_active": True,
                    "phone": self._demo_phone(),
                },
            )
            profiles.append(profile)
            if profile_created:
                self.stats["profiles"] += 1

        self.stdout.write(
            f"  Created {len(profiles)} profiles (1 admin, {user_count} users)"
        )
        return profiles

    def create_teams(self, org, profiles, count):
        """Create teams and assign random profiles."""
        teams = []
        team_templates = random.sample(
            self.TEAM_NAMES, min(count, len(self.TEAM_NAMES))
        )

        for name, description in team_templates:
            team = Teams.objects.create(
                name=name,
                description=description,
                org=org,
            )
            # Assign random profiles to team
            num_members = random.randint(1, min(3, len(profiles)))
            team.users.add(*random.sample(profiles, num_members))
            teams.append(team)
            self.stats["teams"] += 1

        self.stdout.write(f"  Created {len(teams)} teams")
        return teams

    def create_tags(self, org, count):
        """Create tags for the organization."""
        tags = []
        tag_names = random.sample(self.TAG_NAMES, min(count, len(self.TAG_NAMES)))

        for name in tag_names:
            tag = Tags.objects.create(name=name, org=org)
            tags.append(tag)
            self.stats["tags"] += 1

        self.stdout.write(f"  Created {len(tags)} tags")
        return tags

    def create_contacts(self, org, profiles, teams, tags, count):
        """Create contacts."""
        from contacts.models import Contact

        contacts = []
        for _ in range(count):
            first = self._demo_first_name()
            last = self._demo_last_name()
            contact = Contact.objects.create(
                first_name=first,
                last_name=last,
                email=self._demo_person_email(first, last),
                phone=self._demo_phone(),
                organization=self._demo_company(),
                title=self._demo_job_title(),
                department=random.choice(
                    ["Sales", "Marketing", "Engineering", "Finance", "Operations", "HR"]
                ),
                address_line=self._demo_street_address(),
                city=self.fake.city(),
                state=self.fake.state_abbr(),
                postcode=self.fake.postcode(),
                country=org.default_country or "US",
                description=self.fake.paragraph() if random.random() > 0.5 else None,
                org=org,
            )

            # Add M2M relationships
            self._add_assignments(contact, profiles, teams, tags)
            contacts.append(contact)
            self.stats["contacts"] += 1

        self.stdout.write(f"  Created {len(contacts)} contacts")
        return contacts

    def create_accounts(self, org, profiles, teams, tags, contacts, count):
        """Create accounts with contacts."""
        from accounts.models import Account

        accounts = []
        industries = [c[0] for c in INDCHOICES]
        # Reserve a unique demo company name per account in this org so the
        # account list never shows duplicate names.
        account_names = self._demo_unique_companies(count)

        for account_name in account_names:
            account = Account.objects.create(
                name=account_name,
                email=self._demo_company_email(account_name),
                phone=self._demo_phone(),
                website=self._demo_website(account_name),
                industry=random.choice(industries),
                number_of_employees=random.choice(
                    [10, 50, 100, 250, 500, 1000, 5000, 10000]
                ),
                annual_revenue=Decimal(str(random.randint(100000, 10000000))),
                currency=org.default_currency,
                address_line=self._demo_street_address(),
                city=self.fake.city(),
                state=self.fake.state_abbr(),
                postcode=self.fake.postcode(),
                country=org.default_country or "US",
                description=self.fake.paragraph() if random.random() > 0.5 else None,
                org=org,
            )

            # Add contacts to account
            if contacts:
                num_contacts = random.randint(1, min(3, len(contacts)))
                account.contacts.add(*random.sample(contacts, num_contacts))

            # Add M2M relationships
            self._add_assignments(account, profiles, teams, tags)
            accounts.append(account)
            self.stats["accounts"] += 1

        self.stdout.write(f"  Created {len(accounts)} accounts")
        return accounts

    def create_leads(self, org, profiles, teams, tags, contacts, count):
        """Create leads."""
        from leads.models import Lead

        leads = []
        industries = [c[0] for c in INDCHOICES]
        sources = [c[0] for c in LEAD_SOURCE]
        ratings = ["HOT", "WARM", "COLD"]

        # Lead title templates for realistic data
        lead_title_templates = [
            "Enterprise Deal",
            "Website Inquiry",
            "Demo Request",
            "Partnership Opportunity",
            "Inbound Lead",
            "Referral Opportunity",
            "Trade Show Contact",
            "Product Inquiry",
            "Upgrade Opportunity",
            "Expansion Deal",
            "New Business Inquiry",
            "Consultation Request",
        ]

        for _ in range(count):
            status = self._weighted_choice(self.LEAD_STATUS_WEIGHTS)
            # Generate a title - either from templates or using faker
            if random.random() > 0.3:
                title = random.choice(lead_title_templates)
            else:
                title = f"{self.fake.catch_phrase()} Opportunity"

            first = self._demo_first_name()
            last = self._demo_last_name()
            company_name = self._demo_company()
            lead = Lead.objects.create(
                title=title,
                first_name=first,
                last_name=last,
                email=self._demo_person_email(first, last),
                phone=self._demo_phone(),
                company_name=company_name,
                job_title=self._demo_job_title(),
                website=(
                    self._demo_website(company_name) if random.random() > 0.5 else None
                ),
                status=status,
                source=random.choice(sources),
                industry=random.choice(industries),
                rating=random.choice(ratings),
                opportunity_amount=Decimal(str(random.randint(5000, 500000))),
                currency=org.default_currency,
                probability=random.randint(10, 90),
                close_date=(
                    self.fake.date_between(start_date="today", end_date="+90d")
                    if random.random() > 0.3
                    else None
                ),
                address_line=self._demo_street_address(),
                city=self.fake.city(),
                state=self.fake.state_abbr(),
                postcode=self.fake.postcode(),
                country=org.default_country or "US",
                description=self.fake.paragraph() if random.random() > 0.5 else None,
                org=org,
            )

            # Optionally link contacts
            if contacts and random.random() > 0.7:
                num_contacts = random.randint(1, min(2, len(contacts)))
                lead.contacts.add(*random.sample(contacts, num_contacts))

            # Add M2M relationships
            self._add_assignments(lead, profiles, teams, tags)
            leads.append(lead)
            self.stats["leads"] += 1

        self.stdout.write(f"  Created {len(leads)} leads")
        return leads

    def create_opportunities(
        self, org, profiles, teams, tags, contacts, accounts, count
    ):
        """Create opportunities linked to accounts."""
        from opportunity.models import DealPipeline, Opportunity
        from opportunity.workflow import CLOSED_KINDS

        # OPP_STAGE_WEIGHTS names the codes a default pipeline is seeded with;
        # whether one is closed is its stage's kind.
        closed_codes = {
            stage.code
            for stage in DealPipeline.default_for(org).stages.all()
            if stage.kind in CLOSED_KINDS
        }
        opportunities = []
        opp_types = [c[0] for c in OPPORTUNITY_TYPES]
        sources = [c[0] for c in SOURCES]

        for _ in range(count):
            stage = self._weighted_choice(self.OPP_STAGE_WEIGHTS)
            account = random.choice(accounts) if accounts else None

            opp = Opportunity.objects.create(
                name=f"{self.fake.catch_phrase()} Deal",
                account=account,
                stage=stage,
                opportunity_type=random.choice(opp_types),
                amount=Decimal(str(random.randint(10000, 1000000))),
                currency=org.default_currency,
                probability=self._stage_to_probability(stage),
                closed_on=(
                    self.fake.date_between(start_date="today", end_date="+120d")
                    if stage not in closed_codes
                    else self.fake.date_between(start_date="-30d", end_date="today")
                ),
                lead_source=random.choice(sources),
                description=self.fake.paragraph() if random.random() > 0.5 else None,
                org=org,
            )

            # Set closed_by for closed opportunities
            if stage in closed_codes:
                opp.closed_by = random.choice(profiles)
                opp.save()

            # Link contacts
            if contacts:
                num_contacts = random.randint(1, min(2, len(contacts)))
                opp.contacts.add(*random.sample(contacts, num_contacts))

            # Add M2M relationships
            self._add_assignments(opp, profiles, teams, tags)
            opportunities.append(opp)
            self.stats["opportunities"] += 1

        self.stdout.write(f"  Created {len(opportunities)} opportunities")
        return opportunities

    def create_cases(self, org, profiles, teams, tags, contacts, accounts, count):
        """Create cases linked to accounts."""
        from cases.models import Case

        cases = []
        case_types = [c[0] for c in CASE_TYPE]
        priorities = [c[0] for c in PRIORITY_CHOICE]

        for _ in range(count):
            status = self._weighted_choice(self.CASE_STATUS_WEIGHTS)
            account = random.choice(accounts) if accounts else None

            case = Case.objects.create(
                name=f"{self.fake.bs().title()} Issue"[:64],
                status=status,
                priority=random.choice(priorities),
                case_type=random.choice(case_types),
                account=account,
                closed_on=(
                    self.fake.date_between(start_date="-30d", end_date="today")
                    if status == "Closed"
                    else None
                ),
                description=self.fake.paragraph(),
                org=org,
            )

            # Link contacts
            if contacts:
                num_contacts = random.randint(1, min(2, len(contacts)))
                case.contacts.add(*random.sample(contacts, num_contacts))

            # Add M2M relationships
            self._add_assignments(case, profiles, teams, tags)
            cases.append(case)
            self.stats["cases"] += 1

        self.stdout.write(f"  Created {len(cases)} cases")
        return cases

    def create_sales_goals(self, org, profiles, teams, count):
        """Create sales goals/quotas across monthly, quarterly, and yearly periods.

        Period dates anchor to the calendar boundaries containing today so that
        SalesGoal.compute_progress() picks up the seeded CLOSED_WON opportunities
        (whose closed_on lands in the trailing 30 days). Targets vary widely so
        the resulting status mix spans behind / at_risk / on_track / completed.
        Assignment is split ~60% to a profile, ~30% to a team, ~10% org-wide.
        """
        import calendar
        from datetime import date

        from opportunity.models import SalesGoal

        today = timezone.localdate()
        year, month = today.year, today.month
        month_names = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        quarter = (month - 1) // 3 + 1
        q_first_month = (quarter - 1) * 3 + 1

        month_last = calendar.monthrange(year, month)[1]
        q_last = calendar.monthrange(year, q_first_month + 2)[1]

        # (period_type, start, end, label-for-name)
        period_specs = [
            (
                "MONTHLY",
                date(year, month, 1),
                date(year, month, month_last),
                f"{month_names[month - 1]} {year}",
            ),
            (
                "QUARTERLY",
                date(year, q_first_month, 1),
                date(year, q_first_month + 2, q_last),
                f"Q{quarter} {year}",
            ),
            ("YEARLY", date(year, 1, 1), date(year, 12, 31), f"{year} Annual"),
        ]
        revenue_name_tpl = {
            "MONTHLY": "{period} Revenue Push",
            "QUARTERLY": "{period} Revenue Quota",
            "YEARLY": "{period} Revenue Target",
        }
        deals_name_tpl = {
            "MONTHLY": "{period} New Logo Drive",
            "QUARTERLY": "{period} Deals Target",
            "YEARLY": "{period} Logos Goal",
        }
        revenue_range = {
            "MONTHLY": (25_000, 250_000),
            "QUARTERLY": (100_000, 1_000_000),
            "YEARLY": (500_000, 5_000_000),
        }
        deals_range = {
            "MONTHLY": (3, 20),
            "QUARTERLY": (10, 60),
            "YEARLY": (50, 200),
        }

        goals = []
        for _ in range(count):
            period_type, p_start, p_end, p_label = random.choices(
                period_specs, weights=[5, 3, 1], k=1
            )[0]
            goal_type = random.choices(
                ["REVENUE", "DEALS_CLOSED"], weights=[6, 4], k=1
            )[0]

            if goal_type == "REVENUE":
                low, high = revenue_range[period_type]
                # Round to the nearest $1,000 so seeded targets look intentional.
                target = Decimal(str(random.randint(low // 1000, high // 1000) * 1000))
                name = revenue_name_tpl[period_type].format(period=p_label)
            else:
                low, high = deals_range[period_type]
                target = Decimal(str(random.randint(low, high)))
                name = deals_name_tpl[period_type].format(period=p_label)

            roll = random.random()
            assigned_to = None
            team = None
            if roll < 0.6 and profiles:
                assigned_to = random.choice(profiles)
            elif roll < 0.9 and teams:
                team = random.choice(teams)
            # else: ~10% org-wide (no profile, no team)

            goal = SalesGoal.objects.create(
                name=name,
                goal_type=goal_type,
                target_value=target,
                period_type=period_type,
                period_start=p_start,
                period_end=p_end,
                assigned_to=assigned_to,
                team=team,
                is_active=random.random() > 0.05,
                org=org,
            )
            goals.append(goal)
            self.stats["goals"] += 1

        self.stdout.write(f"  Created {len(goals)} sales goals")
        return goals

    def create_tasks(
        self,
        org,
        profiles,
        teams,
        tags,
        contacts,
        accounts,
        leads,
        opportunities,
        cases,
        count,
    ):
        """Create tasks linked to various parent entities."""
        from tasks.models import Task

        tasks = []
        priorities = ["Low", "Medium", "High"]

        for _ in range(count):
            status = self._weighted_choice(self.TASK_STATUS_WEIGHTS)

            # Determine parent entity (only one allowed)
            parent_type = random.choices(
                ["account", "opportunity", "case", "lead", "none"],
                weights=[30, 25, 25, 15, 5],
                k=1,
            )[0]

            task_data = {
                "title": self.fake.sentence(nb_words=5)[:200],
                "status": status,
                "priority": random.choice(priorities),
                "due_date": (
                    self.fake.date_between(start_date="today", end_date="+30d")
                    if status != "Completed"
                    else self.fake.date_between(start_date="-14d", end_date="today")
                ),
                "description": self.fake.paragraph() if random.random() > 0.5 else None,
                "org": org,
            }

            # Set parent entity
            if parent_type == "account" and accounts:
                task_data["account"] = random.choice(accounts)
            elif parent_type == "opportunity" and opportunities:
                task_data["opportunity"] = random.choice(opportunities)
            elif parent_type == "case" and cases:
                task_data["case"] = random.choice(cases)
            elif parent_type == "lead" and leads:
                task_data["lead"] = random.choice(leads)

            task = Task.objects.create(**task_data)

            # Link contacts
            if contacts and random.random() > 0.7:
                num_contacts = random.randint(1, min(2, len(contacts)))
                task.contacts.add(*random.sample(contacts, num_contacts))

            # Add M2M relationships
            self._add_assignments(task, profiles, teams, tags)
            tasks.append(task)
            self.stats["tasks"] += 1

        self.stdout.write(f"  Created {len(tasks)} tasks")
        return tasks

    # ------------------------------------------------------------------
    # Demo-safe value generators
    # All identifying values flow through these helpers so seeded data
    # stays unmistakably fictional: names from curated pools, emails on
    # the reserved example.com / .example TLDs, phone numbers in the NANP
    # fictional 555-555-01XX range, and synthesized street addresses.
    # ------------------------------------------------------------------
    def _demo_first_name(self):
        return random.choice(self.DEMO_FIRST_NAMES)

    def _demo_last_name(self):
        return random.choice(self.DEMO_LAST_NAMES)

    def _demo_company(self):
        return random.choice(self.DEMO_COMPANIES)

    def _demo_unique_companies(self, count):
        """Return `count` distinct demo company names.

        With ``count <= len(pool)``, returns a random sample without
        replacement so a single org's accounts never collide on name.
        Beyond the pool size, falls back to regional/structural suffixes
        ("Northwind Cloud Holdings", "Northwind Cloud North") so any
        duplicates still read as separate entities.
        """
        pool = list(self.DEMO_COMPANIES)
        if count <= len(pool):
            return random.sample(pool, count)
        base = random.sample(pool, len(pool))
        suffixes = [
            "Group",
            "Holdings",
            "International",
            "North",
            "South",
            "East",
            "West",
            "Central",
            "Pacific",
            "Atlantic",
        ]
        extras = []
        for i in range(count - len(pool)):
            extras.append(f"{random.choice(pool)} {suffixes[i % len(suffixes)]}")
        return base + extras

    def _demo_job_title(self):
        return random.choice(self.DEMO_JOB_TITLES)

    def _company_slug(self, name):
        """Reduce a company name to a URL/email-safe slug."""
        slug = "".join(ch.lower() for ch in name if ch.isalnum())
        return slug or "demo"

    def _demo_person_email(self, first, last):
        """Build a person email on the reserved example.com demo domain.

        A 4-digit numeric suffix keeps collisions rare across large seeds
        without forcing strict uniqueness tracking.
        """
        return f"{first.lower()}.{last.lower()}{random.randint(1, 9999)}@example.com"

    def _demo_company_email(self, company_name):
        """Build a generic contact email on the reserved .example TLD."""
        slug = self._company_slug(company_name)
        prefix = random.choice(["info", "contact", "sales", "hello", "team"])
        return f"{prefix}@{slug}.example"

    def _demo_website(self, company_name=None):
        """Build a company website URL on the reserved .example TLD."""
        if company_name is None:
            company_name = self._demo_company()
        return f"https://www.{self._company_slug(company_name)}.example"

    def _demo_phone(self):
        """Return a NANP fictional-range phone number (555-555-0100..0199)."""
        return f"+1 555-555-01{random.randint(0, 99):02d}"

    def _demo_street_address(self):
        """Synthesize a street address from a curated street-name pool."""
        number = random.randint(100, 9999)
        street = random.choice(self.DEMO_STREET_NAMES)
        if random.random() > 0.6:
            return f"{number} {street}, Suite {random.randint(100, 999)}"
        return f"{number} {street}"

    def _add_assignments(self, instance, profiles, teams, tags):
        """Add common M2M assignments to an entity."""
        # Assign to profiles
        if profiles:
            num_assigned = random.randint(1, min(2, len(profiles)))
            instance.assigned_to.add(*random.sample(profiles, num_assigned))

        # Assign to team (70% chance)
        if teams and random.random() > 0.3:
            instance.teams.add(random.choice(teams))

        # Add tags (60% chance)
        if tags and random.random() > 0.4:
            num_tags = random.randint(1, min(3, len(tags)))
            instance.tags.add(*random.sample(tags, num_tags))

    def _weighted_choice(self, weights_dict):
        """Select a random key based on weights."""
        items = list(weights_dict.keys())
        weights = list(weights_dict.values())
        return random.choices(items, weights=weights, k=1)[0]

    def _stage_to_probability(self, stage):
        """Map opportunity stage to probability."""
        mapping = {
            "PROSPECTING": random.randint(5, 15),
            "QUALIFICATION": random.randint(15, 30),
            "PROPOSAL": random.randint(30, 50),
            "NEGOTIATION": random.randint(50, 75),
            "CLOSED_WON": 100,
            "CLOSED_LOST": 0,
        }
        return mapping.get(stage, 50)

    def print_summary(self, elapsed_seconds):
        """Print seeding summary."""
        # Merge invoice stats
        self.stats.update(self.invoice_seeder.stats)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seeding complete!"))
        self.stdout.write("")
        self.stdout.write("Summary:")
        self.stdout.write(f"  Organizations: {self.stats['orgs']}")
        self.stdout.write(f"  Users: {self.stats['users']}")
        self.stdout.write(f"  Profiles: {self.stats['profiles']}")
        self.stdout.write(f"  Teams: {self.stats['teams']}")
        self.stdout.write(f"  Tags: {self.stats['tags']}")
        self.stdout.write(f"  Contacts: {self.stats['contacts']}")
        self.stdout.write(f"  Accounts: {self.stats['accounts']}")
        self.stdout.write(f"  Products: {self.stats['products']}")
        self.stdout.write(f"  Invoice Templates: {self.stats['invoice_templates']}")
        self.stdout.write(f"  Leads: {self.stats['leads']}")
        self.stdout.write(f"  Opportunities: {self.stats['opportunities']}")
        self.stdout.write(f"  Invoices: {self.stats['invoices']}")
        self.stdout.write(f"  Invoice Line Items: {self.stats['invoice_line_items']}")
        self.stdout.write(f"  Payments: {self.stats['payments']}")
        self.stdout.write(f"  Estimates: {self.stats['estimates']}")
        self.stdout.write(f"  Estimate Line Items: {self.stats['estimate_line_items']}")
        self.stdout.write(f"  Recurring Invoices: {self.stats['recurring_invoices']}")
        self.stdout.write(f"  Cases: {self.stats['cases']}")
        self.stdout.write(f"  Tasks: {self.stats['tasks']}")
        self.stdout.write(f"  Sales Goals: {self.stats['goals']}")
        self.stdout.write("")
        self.stdout.write(f"Total time: {elapsed_seconds:.2f} seconds")

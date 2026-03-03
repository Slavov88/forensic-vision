"""
Management command: seed_demo
Creates a demo user and demo case for quick-start testing.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import Case, CaseMembership

User = get_user_model()


class Command(BaseCommand):
    help = "Създава демо потребител и демо кейс за тестване."

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            username="demo",
            defaults={
                "email": "demo@forensic.local",
                "first_name": "Demo",
                "last_name": "User",
                "is_staff": False,
                "is_active": True,
            },
        )
        if created:
            user.set_password("demo1234")
            user.save()
            self.stdout.write(self.style.SUCCESS("✔ Създаден демо потребител: demo / demo1234"))
        else:
            self.stdout.write("ℹ Демо потребителят вече съществува: demo")

        case, created = Case.objects.get_or_create(
            title="Демо кейс – Примерно разследване",
            created_by=user,
            defaults={
                "description": (
                    "Автоматично генериран демо кейс за тестване на платформата. "
                    "Можете да качите PDF или изображение и да стартирате анализ."
                ),
                "status": Case.Status.DRAFT,
                "tags": ["демо", "тест"],
            },
        )

        if created:
            # Add demo user as editor member
            CaseMembership.objects.get_or_create(
                case=case,
                user=user,
                defaults={"role": CaseMembership.Role.EDITOR, "invited_by": user},
            )
            self.stdout.write(
                self.style.SUCCESS(f"✔ Създаден демо кейс (ID={case.pk}): {case.title}")
            )
        else:
            self.stdout.write(f"ℹ Демо кейсът вече съществува (ID={case.pk})")

        self.stdout.write(
            self.style.SUCCESS(
                "\n── Seed завършен ──\n"
                f"  Потребител : demo / demo1234\n"
                f"  Кейс ID    : {case.pk}\n"
                f"  API Token  : POST /api/auth/token/ с горните данни\n"
            )
        )

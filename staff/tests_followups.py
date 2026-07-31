"""
اختبارات مرحلة 7 (صفحة "المتابعات" في لوحة الموظف — ROADMAP.md): فلترة
"متابعاتي/الكل" و"مفتوحة/متأخرة/منجزة"، وصلاحية الوصول للصفحة.
"""
from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountType, ClientProfile, User
from followups.models import FollowUp
from followups.services import create_followup, mark_done


def make_admin(username='admin1'):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=User.Role.ADMIN, status=User.Status.ACTIVE,
    )


def make_client_profile(username='client1'):
    account_type, _ = AccountType.objects.get_or_create(name='جملة')
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=User.Role.CLIENT,
    )
    return ClientProfile.objects.create(
        user=user, business_name='محل تجريبي', account_type=account_type,
        address='القاهرة', phone='01000000000',
    )


class FollowUpListViewTestCase(TestCase):
    def setUp(self):
        self.admin1 = make_admin('admin1')
        self.admin2 = make_admin('admin2')
        self.profile = make_client_profile()
        self.http = HttpClient()
        self.http.force_login(self.admin1)

    def test_client_role_is_redirected(self):
        client_http = HttpClient()
        client_http.force_login(self.profile.user)
        response = client_http.get(reverse('staff:followup_list'))
        self.assertEqual(response.status_code, 302)

    def test_default_scope_shows_only_mine(self):
        create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.admin1,
        )
        create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.admin2,
        )
        response = self.http.get(reverse('staff:followup_list'))
        self.assertEqual(len(response.context['followups']), 1)

    def test_scope_all_shows_every_employees_followups(self):
        create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.admin1,
        )
        create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.admin2,
        )
        response = self.http.get(reverse('staff:followup_list'), {'scope': 'all'})
        self.assertEqual(len(response.context['followups']), 2)

    def test_status_done_filter_excludes_open_ones(self):
        open_one = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.admin1,
        )
        done_one = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.VISIT,
            due_date=timezone.localdate(), assigned_to=self.admin1,
        )
        mark_done(done_one, self.admin1)
        response = self.http.get(reverse('staff:followup_list'), {'status': 'done'})
        ids = {f.pk for f in response.context['followups']}
        self.assertEqual(ids, {done_one.pk})
        self.assertNotIn(open_one.pk, ids)

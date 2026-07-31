"""
اختبارات مرحلة 7 (متابعات مجدولة — ROADMAP.md): جدولة متابعة على أي
كيان، تعليمها منجزة، عزل المتابعات بين كيانات مختلفة، وصلاحيات الوصول.
"""
from datetime import timedelta

from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountType, ClientProfile, User
from .models import FollowUp
from .services import create_followup, followups_for, mark_done, open_followups_count_for


def make_staff(role=User.Role.WAREHOUSE, username=None):
    username = username or f'staff_{role.lower()}'
    return User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=role, status=User.Status.ACTIVE,
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


class FollowUpServicesTestCase(TestCase):
    def setUp(self):
        self.employee = make_staff()
        self.profile = make_client_profile()

    def test_create_followup_links_to_instance_via_content_type(self):
        followup = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.employee,
        )
        self.assertEqual(followup.content_object, self.profile)
        self.assertFalse(followup.is_done)

    def test_followups_for_isolates_different_instances(self):
        """متابعة على عميل مايفترضش تظهر في متابعات عميل تاني."""
        other_profile = make_client_profile(username='client2')
        create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.employee,
        )
        self.assertEqual(followups_for(self.profile).count(), 1)
        self.assertEqual(followups_for(other_profile).count(), 0)

    def test_mark_done_sets_done_at_and_done_by(self):
        followup = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.employee,
        )
        mark_done(followup, self.employee)
        followup.refresh_from_db()
        self.assertTrue(followup.is_done)
        self.assertEqual(followup.done_by, self.employee)

    def test_is_overdue_true_only_when_open_and_past_due(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        overdue = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=yesterday, assigned_to=self.employee,
        )
        self.assertTrue(overdue.is_overdue)
        mark_done(overdue, self.employee)
        self.assertFalse(overdue.is_overdue, 'المتابعة المنجزة مش المفروض تتحسب متأخرة حتى لو استحقاقها فات')

    def test_open_followups_count_excludes_done(self):
        create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.employee,
        )
        done_one = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.VISIT,
            due_date=timezone.localdate(), assigned_to=self.employee,
        )
        mark_done(done_one, self.employee)
        self.assertEqual(open_followups_count_for(self.profile), 1)


class FollowUpViewsTestCase(TestCase):
    def setUp(self):
        self.employee = make_staff(role=User.Role.ADMIN, username='admin1')
        self.profile = make_client_profile()
        self.http = HttpClient()
        self.http.login(username='admin1', password='testpass123')

    def _add_url(self):
        return reverse('followups:followup_add', args=['accounts', 'clientprofile', self.profile.pk])

    def test_followup_add_creates_followup(self):
        response = self.http.post(self._add_url(), {
            'activity_type': FollowUp.ActivityType.CALL,
            'due_date': timezone.localdate().isoformat(),
            'assigned_to': self.employee.pk,
            'note': 'اتصال متابعة',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FollowUp.objects.count(), 1)
        self.assertEqual(FollowUp.objects.first().note, 'اتصال متابعة')

    def test_followup_add_rejects_missing_due_date(self):
        response = self.http.post(self._add_url(), {
            'activity_type': FollowUp.ActivityType.CALL,
            'due_date': '',
            'assigned_to': self.employee.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FollowUp.objects.count(), 0)

    def test_followup_add_rejects_inactive_or_client_assignee(self):
        """العميل نفسه (أو موظف غير نشط) مايصحش يتكلّف بمتابعة."""
        client_user = self.profile.user
        response = self.http.post(self._add_url(), {
            'activity_type': FollowUp.ActivityType.CALL,
            'due_date': timezone.localdate().isoformat(),
            'assigned_to': client_user.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FollowUp.objects.count(), 0)

    def test_client_cannot_schedule_followup(self):
        """العميل مش موظف — مفروض يترفض حتى لو حاول يبعت الفورم مباشرة."""
        client_http = HttpClient()
        client_http.login(username=self.profile.user.username, password='testpass123')
        response = client_http.post(self._add_url(), {
            'activity_type': FollowUp.ActivityType.CALL,
            'due_date': timezone.localdate().isoformat(),
            'assigned_to': self.employee.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FollowUp.objects.count(), 0)

    def test_followup_done_marks_completed(self):
        followup = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.employee,
        )
        response = self.http.post(reverse('followups:followup_done', args=[followup.pk]))
        self.assertEqual(response.status_code, 302)
        followup.refresh_from_db()
        self.assertTrue(followup.is_done)

    def test_followup_delete_removes_it(self):
        followup = create_followup(
            self.profile, activity_type=FollowUp.ActivityType.CALL,
            due_date=timezone.localdate(), assigned_to=self.employee,
        )
        response = self.http.post(reverse('followups:followup_delete', args=[followup.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FollowUp.objects.count(), 0)

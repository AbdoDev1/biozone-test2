from django.contrib import admin

from .models import FollowUp


@admin.register(FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ('activity_type', 'content_type', 'object_id', 'due_date', 'assigned_to', 'done_at')
    list_filter = ('activity_type', 'content_type', 'assigned_to')
    search_fields = ('note',)

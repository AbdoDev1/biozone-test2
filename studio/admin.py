from django.contrib import admin

from .models import LandingPageSettings, StudioFolder, StudioImage


@admin.register(StudioImage)
class StudioImageAdmin(admin.ModelAdmin):
    list_display = ('id', 'original_filename', 'folder', 'uploaded_by', 'uploaded_at')
    list_filter = ('uploaded_by', 'folder')
    search_fields = ('original_filename',)
    readonly_fields = ('uploaded_at',)


@admin.register(StudioFolder)
class StudioFolderAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'created_at')
    search_fields = ('name',)
    readonly_fields = ('created_at',)


@admin.register(LandingPageSettings)
class LandingPageSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'hero_image', 'banner_1', 'banner_2', 'updated_at')
    readonly_fields = ('updated_at',)

    def has_add_permission(self, request):
        return not LandingPageSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

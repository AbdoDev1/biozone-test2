from django.contrib import admin

from .models import StudioFolder, StudioImage


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

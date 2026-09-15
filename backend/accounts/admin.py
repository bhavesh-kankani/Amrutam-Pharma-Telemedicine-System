from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.core import exceptions as core_exceptions

from .models import Profile, Doctor

User = get_user_model()


class UserCreationForm(forms.ModelForm):
    """
    Form used specifically on the /admin/accounts/user/add/ creation page.
    Enforces password matching and validates against all Django password validators.
    """
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput,
        help_text=password_validation.password_validators_help_text_html(),
    )
    password_confirmation = forms.CharField(
        label="Password confirmation",
        widget=forms.PasswordInput,
        help_text="Enter the same password as above for verification.",
    )

    class Meta:
        model = User
        fields = ("email", "role", "is_staff", "is_superuser")

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirmation = cleaned_data.get("password_confirmation")

        # 1. Validate matching confirmation
        if password and password_confirmation and password != password_confirmation:
            self.add_error("password_confirmation", "Passwords do not match.")

        # 2. Run all Django AUTH_PASSWORD_VALIDATORS on password
        if password:
            try:
                # Pass instance with email so UserAttributeSimilarityValidator can check similarity
                user = self.instance
                if user is not None:
                    user.email = cleaned_data.get("email", "")
                password_validation.validate_password(password, user=user)
            except core_exceptions.ValidationError as error:
                self.add_error("password", error)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        # Hash plaintext password into PBKDF2/Argon2 before DB write
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    """
    Form used on the edit/update user page.
    Protects password hash from direct modification.
    """
    password = ReadOnlyPasswordHashField(
        help_text=(
            "Raw passwords are not stored. Use "
            '<a href="../password/">this form</a> to change the password.'
        )
    )

    class Meta:
        model = User
        fields = ("email", "password", "role", "is_active", "is_staff", "is_superuser")

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and User.objects.exclude(pk=self.instance.pk).filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def clean_password(self):
        # Always return initial password hash; password changes go through password view
        return self.initial.get("password")


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    # Hook up the custom forms
    add_form = UserCreationForm
    form = UserChangeForm

    list_display = ("email", "role", "is_staff", "mfa_enabled", "created_at")
    list_filter = ("role", "is_staff", "mfa_enabled")
    ordering = ("email",)
    search_fields = ("email",)

    # Fields visible when EDITING an existing user
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Role & Security", {"fields": ("role", "mfa_enabled")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )

    # Fields visible when CREATING a new user (/add/)
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password", "password_confirmation", "role", "is_staff", "is_superuser"),
            },
        ),
    )


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "user", "date_of_birth")
    search_fields = ("first_name", "last_name", "user__email")


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("license_number", "user", "specialization", "consultation_fee", "is_verified")
    list_filter = ("specialization", "is_verified")
    search_fields = ("license_number", "user__email", "specialization")

from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from .models import User, ClientProfile, AccountType

INPUT_CLASSES = (
    'w-full border border-gray-300 rounded-lg px-4 py-2 text-sm '
    'focus:outline-none focus:ring-2 focus:ring-blue-500'
)
# نفس الكلاسات فوق + مساحة إضافية على جهة النهاية (يمين في RTL) عشان
# أيقونة "إظهار كلمة المرور" متتلخبطش مع النص وقت الكتابة.
PASSWORD_INPUT_CLASSES = INPUT_CLASSES + ' pe-10'


class ClientPasswordChangeForm(PasswordChangeForm):
    """
    نفس فورم Django القياسي لتغيير كلمة المرور، بس بعناوين عربية
    وكلاسات Tailwind عشان تتوافق مع باقي فورمات الحساب بدل شكل
    Django الافتراضي.
    """
    old_password = forms.CharField(
        label='كلمة المرور الحالية',
        widget=forms.PasswordInput(attrs={
            'class': PASSWORD_INPUT_CLASSES,
            # x-bind:type بيتقرا كـ خاصية HTML عادية من Django (متعرفش
            # إنها Alpine)، وAlpine بيلقطها من الـ DOM بعد ما الصفحة
            # تحمّل. لازم القالب اللي بيعرض الحقل ده يكون جواه عنصر أب
            # فيه x-data="{ show: false }" عشان show تتعرف. راجع
            # dashboard.html للف اللي بيلف على password_form.
            'x-bind:type': "show ? 'text' : 'password'",
        }),
    )
    new_password1 = forms.CharField(
        label='كلمة المرور الجديدة',
        widget=forms.PasswordInput(attrs={
            'class': PASSWORD_INPUT_CLASSES,
            'x-bind:type': "show ? 'text' : 'password'",
        }),
    )
    new_password2 = forms.CharField(
        label='تأكيد كلمة المرور الجديدة',
        widget=forms.PasswordInput(attrs={
            'class': PASSWORD_INPUT_CLASSES,
            'x-bind:type': "show ? 'text' : 'password'",
        }),
    )


class RegisterForm(UserCreationForm):
    business_name = forms.CharField(max_length=255, label='اسم النشاط التجاري')
    account_type = forms.ModelChoiceField(
        queryset=AccountType.objects.filter(is_active=True),
        label='نوع الحساب',
        # كان empty_label=None (بلا خيار فاضي) بهدف إجبار العميل يختار
        # نوع حساب فعلي — لكن ده كان بيرجع بنتيجة عكسية تمامًا: بلا خيار
        # فاضي في الأول، والـ template (accounts/register.html) عنده خطأ
        # منفصل بيمنع أي option من أخد selected أصلًا (راجع تعليق الـ
        # template)، فالمتصفح كان بيعرض أول نوع أبجديًا كـ"مختار" افتراضيًا
        # بلا ما العميل يلمسه خالص — أي عميل مش منتبه بيتسجل بنوع حساب
        # غلط تمامًا بدون أي تحذير. رجّعنا خيار فاضي واضح بدل None، عشان
        # الفورم يرفض الإرسال فعليًا لو العميل ماضغطش يختار نوع حقيقي.
        empty_label='اختر نوع الحساب...',
    )
    address = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), label='العنوان')
    phone = forms.CharField(max_length=20, label='رقم الهاتف')

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CLIENT
        user.status = User.Status.PENDING
        if commit:
            user.save()
            ClientProfile.objects.create(
                user=user,
                business_name=self.cleaned_data['business_name'],
                account_type=self.cleaned_data['account_type'],
                address=self.cleaned_data['address'],
                phone=self.cleaned_data['phone'],
            )
        return user


class LoginForm(forms.Form):
    username = forms.CharField(label='اسم المستخدم')
    password = forms.CharField(widget=forms.PasswordInput, label='كلمة المرور')

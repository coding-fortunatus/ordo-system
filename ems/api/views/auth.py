from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from ems.api.serializers.user import LoginSerializer, UserSerializer
from tenancy.observability import metrics


class LoginView(APIView):
    """POST /api/auth/login/ — returns {token, user}."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            metrics.LOGINS.add(1, outcome="failed", via="api")
            raise ValidationError(serializer.errors)
        metrics.LOGINS.add(1, outcome="succeeded", via="api")
        user = serializer.validated_data["user"]
        # Single session per account: rotate the token on every login so any
        # previously signed-in device is invalidated (its next request 401s and
        # the SPA bounces it to /login). New login always wins.
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        return Response(
            {"token": token.key, "user": UserSerializer(user).data},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """POST /api/auth/logout/ — invalidates the caller's token."""

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """GET /api/auth/me/ — current user, used by the SPA on hard reload."""

    def get(self, request):
        return Response(UserSerializer(request.user).data)

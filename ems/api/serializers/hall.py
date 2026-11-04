from rest_framework import serializers

from ems import seating_rules
from ems.models import Hall


class HallSerializer(serializers.ModelSerializer):
    """A hall is a ``rows x columns`` grid, optionally with a seat ``layout``
    (see ``seating_rules``) for rows of different lengths, aisles or
    pillars.

    Write either ``layout`` (the mask itself) or the shorthand ``row_seats``
    (seats per row, front row first) plus ``align`` (``left``, ``center`` or
    ``right``); the shorthand sets ``rows``, ``columns`` and ``layout``
    together. Send ``layout: null`` to make the hall a full rectangle again.
    """

    # Write-only on input; to_representation fills it from the layout.
    row_seats = serializers.ListField(
        child=serializers.IntegerField(min_value=0),
        required=False,
        allow_empty=False,
        write_only=True,
    )
    align = serializers.ChoiceField(
        choices=seating_rules.ALIGNMENTS, required=False, write_only=True
    )
    seat_count = serializers.IntegerField(read_only=True)
    # Blank = filled from the name on save ("BE 3" -> "BE").
    group = serializers.CharField(max_length=32, required=False, allow_blank=True)

    class Meta:
        model = Hall
        fields = [
            "id",
            "name",
            "group",
            "capacity",
            "rows",
            "columns",
            "layout",
            "seat_order",
            "row_seats",
            "align",
            "seat_count",
            "is_open",
        ]
        read_only_fields = ["id"]

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Hall name is required.")
        qs = Hall.objects.filter(name__iexact=name)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                f"Hall '{name}' already exists."
            )
        return name

    def _positive(self, value, field):
        if value is None or value < 0:
            raise serializers.ValidationError(
                f"{field} must be a non-negative integer."
            )
        return value

    def validate_capacity(self, v):
        return self._positive(v, "Capacity")

    def validate_rows(self, v):
        v = self._positive(v, "Rows")
        if v > seating_rules.MAX_ROWS:
            raise serializers.ValidationError(
                f"A hall can have at most {seating_rules.MAX_ROWS} rows."
            )
        return v

    def validate_columns(self, v):
        v = self._positive(v, "Columns")
        if v > seating_rules.MAX_COLUMNS:
            raise serializers.ValidationError(
                f"A hall can have at most {seating_rules.MAX_COLUMNS} columns."
            )
        return v

    def validate(self, attrs):
        row_seats = attrs.pop("row_seats", None)
        align = attrs.pop("align", "left")
        try:
            if row_seats is not None:
                if attrs.get("layout"):
                    raise ValueError("Send either layout or row_seats, not both.")
                rows, cols, mask = seating_rules.mask_from_row_seats(row_seats, align)
                attrs["rows"], attrs["columns"] = rows, cols
                attrs["layout"] = list(mask) if mask else None
            elif {"rows", "columns", "layout"} & attrs.keys():
                # Check the layout that will be stored against the grid that
                # will be stored, so a partial update of rows or columns can
                # not keep an old layout that no longer fits.
                rows = attrs.get("rows", getattr(self.instance, "rows", 0))
                cols = attrs.get("columns", getattr(self.instance, "columns", 0))
                layout = attrs.get("layout", getattr(self.instance, "layout", None))
                mask = seating_rules.normalise_mask(rows, cols, layout)
                attrs["layout"] = list(mask) if mask else None
        except ValueError as exc:
            raise serializers.ValidationError({"layout": str(exc)}) from exc
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["row_seats"] = seating_rules.row_seats(
            instance.rows or 0,
            instance.columns or 0,
            seating_rules.mask_key(instance.layout),
        )
        return data

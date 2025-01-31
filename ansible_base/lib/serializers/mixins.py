import logging

from rest_framework import serializers

logger = logging.getLogger('ansible_base.lib.serializers.mixins')


class ImmutableFieldsMixin(serializers.ModelSerializer):
    def get_extra_kwargs(self):
        kwargs = super().get_extra_kwargs()
        immutable_fields = getattr(self.Meta, "immutable_fields", None)

        # Make field read_only if instance already exists
        if self.instance and immutable_fields:
            for field in immutable_fields:
                kwargs.setdefault(field, {})
                kwargs[field]["read_only"] = True
        # Make field writable if no instance yet exists
        elif immutable_fields:
            for field in immutable_fields:
                kwargs.setdefault(field, {})
                kwargs[field]["read_only"] = False

        return kwargs

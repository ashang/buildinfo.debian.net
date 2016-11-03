# -*- coding: utf-8 -*-
# Generated by Django 1.10.2 on 2016-11-03 20:39
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('buildinfo_submissions', '0004_populate_keys_key'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='submission',
            name='uid',
        ),
        migrations.AlterField(
            model_name='submission',
            name='key',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='submissions', to='keys.Key'),
        ),
    ]
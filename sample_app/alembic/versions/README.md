<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Sample app migrations

This folder holds the sample app's Alembic revisions. The app owns them, even
the ones that create tables from the `m8flow-core` ORM models, because
the library wheel doesn't ship migrations.

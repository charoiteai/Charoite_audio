---
layout: post
title: "0.102.0: one graph identity for mutations"
date: 2026-11-03 14:30:00 +0300
---

A graph can have more than one spelling: an alias, a case variation or a
Unicode-normalized path may lead to the same directory. If two mutation
commands treat those spellings as different graphs, a lock can protect one
name while the other command writes to the same files.

Charoite 0.102.0 binds mutations to the physical graph directory. The captured
target is carried through the operation instead of being rediscovered from a
path after it changes. Forget plans and affected indexes are refreshed under
the shared graph lock, so the plan being applied matches the protected state.

The release also makes forget-manifest matching exact and keeps malformed
records for manual attention. Dossier revision protection remains active when
its date is damaged. The generator input label moves into the header without
becoming a reason to rebuild an otherwise unchanged dossier.

These are changes to how mutations are admitted and coordinated. They do not
mean that existing stored dossiers were migrated, or that every older
formatting ambiguity has been resolved. The later shared-parser change in
PR902 is outside this release and belongs to the next patch.

The release archive is built from the immutable
[v0.102.0 tag](https://github.com/charoiteai/Charoite_audio/releases/tag/v0.102.0),
not from the newer main branch. The updater will offer it only after its update
manifest has been signed. Apple bundle signing and the separately signed
update manifest remain distinct checks.

## По-русски

В 0.102.0 операции записи привязаны к физической папке графа, а не только к
написанию пути. План забывания и затронутые индексы обновляются под общим
замком. Это закрывает ситуации, когда разные пути ведут к одним файлам, но
операции считают их разными графами.

Также исправлено точное сопоставление путей в манифесте забывания; повреждённые
записи сохраняются для ручной проверки. Защита ревизии действует и при
повреждённой дате. Перенос метки генератора в шапку сам по себе не запускает
пересборку. Миграции существующих досье в рамках этого выпуска нет; изменение
единого парсера из PR902 относится к следующему патчу.

---
layout: post
title: "0.103.0: add to a dossier without losing its history"
date: 2026-11-08 02:50:00 +0300
---

A new meeting should add to a topic's dossier without turning its earlier
history into a fresh summary. [Charoite 0.103.0](https://github.com/charoiteai/Charoite_audio/releases/tag/v0.103.0)
is now available and makes that distinction explicit.

The incremental path gives the local model the existing dossier and the
complete texts of new relevant meetings. Accepted additions retain earlier
claims, canonical links, unresolved warnings and exact author edits. When a
new source explicitly corrects a fact, the accepted correction keeps the
previous claim as attributed history. A separate local judge checks the new
claims' support and relevance before the guarded write.

An oversized input, malformed answer or transport failure leaves the addition
retryable. Unchanged meeting membership makes no model calls; editing an
older source alone does not trigger this incremental path. A full rebuild is
a separate operation. The change does not retrospectively validate all old
claims or migrate existing dossiers.

This builds on the [shared dossier parser in PR902](https://github.com/charoiteai/Charoite_audio/pull/902).
Readers and writers use the same structural interpretation, while keeping
their different write permissions. Ambiguous legacy boundaries leave the
affected file intact instead of guessing where author edits begin. An inline
mention of a protected heading stays text rather than becoming a new section.

Two other changes make failures easier to interpret. The release-signing tool
requires the SHA-256 of the reviewed archive and checks its signing key against
the public key embedded in the app. It refuses different downloaded bytes or
a bundle version that does not match the tag, and verifies the resulting
manifest signature against the same app key. These are safeguards in the
[signer](https://github.com/charoiteai/Charoite_audio/pull/908); signing and
installation remain separate delivery steps.

The nightly dossier report now calls failures **build refusals** and separates
admission, transport, empty replies, format, validation and file errors. Its
aggregate contains counters, not names, paths, prompts or replies. Entering
the streaming adapter is counted separately from completing a request; it
does not prove that an HTTP request reached the server. The existing refusal
threshold stays in place.

Finally, the full Linux pytest job is pinned to Ubuntu 24.04 instead of the
moving `ubuntu-latest` label. The full test command remains unchanged. This
defines the CI baseline without making a new runtime-support claim.

The implementation is recorded in [PR909](https://github.com/charoiteai/Charoite_audio/pull/909)
for incremental history, [PR910](https://github.com/charoiteai/Charoite_audio/pull/910)
for nightly reporting and [PR912](https://github.com/charoiteai/Charoite_audio/pull/912)
for the Linux runner.

## По-русски

Новая встреча должна дополнять досье темы, сохраняя его историю. Выпуск
Charoite 0.103.0 уже опубликован: локальная модель получает прежнее досье
и полные тексты новых релевантных встреч. Принятая добавка сохраняет прежние
утверждения, ссылки, нерешённые предупреждения и точный текст правок автора.
Если новый источник явно исправляет факт, прежнее утверждение остаётся
в истории со ссылкой на источник. Отдельный локальный судья проверяет
обоснованность и релевантность новых утверждений перед записью.

Слишком большой вход, некорректный ответ или ошибка транспорта оставляют
добавку для повтора. При прежнем составе встреч модель не вызывается;
редактирование старого источника само по себе не запускает этот путь.
Полная пересборка остаётся отдельной операцией. Миграции существующих досье
и повторной проверки всех прежних фактов это изменение не выполняет.

Общий парсер из PR902 сохраняет авторские правки и оставляет неоднозначные
старые файлы без автоматической перезаписи. Подписант релиза проверяет хеш
рассмотренного архива, версию приложения и соответствие ключа публичному
якорю в приложении. Ночной отчёт различает стадии и причины отказа сборки,
не включая содержимое документов. Полный Linux-прогон закреплён за Ubuntu
24.04 с прежней командой тестов. Подпись релиза и установка проверяются
отдельно от этих изменений кода.

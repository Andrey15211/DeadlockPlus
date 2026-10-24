// Workflow: DeadlockForge — глобальная горячая клавиша панели поверх игры.
//
// Формат: тело DSH-workflow (глобалы: args, agent, pipeline, phase, log).
// Запуск: инструмент workflow, script = содержимое этого файла,
//         args = { tasks: [{ id, files: [...], spec, model? }] }.
// Верификация (pytest) выполняется в основном агенте после воркеров:
// хук exec в этом движке недоступен.

const tasks = Array.isArray(args.tasks) ? args.tasks : [];
if (tasks.length === 0) throw new Error("args.tasks пуст: нечего исполнять");

const WRITER_PROVIDER = "zexkora-openai";
const WRITER_MODEL = "deepseek-v4-flash";
const REVIEW_PROVIDER = "zexkora-anthropic";
const REVIEW_MODEL = "claude-opus-5.5";

const writeSchema = {
  type: "object",
  properties: {
    ok: { type: "boolean" },
    changedFiles: { type: "array", items: { type: "string" } },
    summary: { type: "string" },
  },
  required: ["ok", "changedFiles", "summary"],
};

const reviewSchema = {
  type: "object",
  properties: {
    ok: { type: "boolean" },
    issues: { type: "array", items: { type: "string" } },
  },
  required: ["ok", "issues"],
};

const writerPrompt = (
  task,
) => `Ты — исполнитель кода в проекте C:\\Projects\\DeadlockForge
(Windows, Python 3.12 в .venv, FastAPI + pywebview, фронт — HTML/CSS/JS без сборки).

Жёсткие правила:
- Работай ТОЛЬКО с файлами из списка. Ничего вне них не создавай и не меняй.
- Никаких TODO, заглушек, print/console.log, пустых веток и «пока так».
- Только stdlib и уже установленные пакеты (fastapi, pydantic, pywebview, pythonnet).
  Новые зависимости запрещены.
- UTF-8 без BOM. Комментарии — на русском, кратко и по делу.
- Существующие файлы правь точечно (edit), не перезаписывай целиком.
- Перед правкой прочитай целевой участок файла (read), иначе edit упадёт по stale-версии.
- Не добавляй проверочных скриптов, стендов и временных файлов.

Файлы задачи: ${JSON.stringify(task.files)}

ЗАДАЧА:
${task.spec}

Верни ok=true только если все правки реально внесены и синтаксис корректен.`;

const reviewPrompt = (
  task,
  written,
) => `Ты — приёмщик кода в проекте C:\\Projects\\DeadlockForge (Windows, Python 3.12).
Прочитай указанные файлы сам и проверь, что задача выполнена по спеке.

Файлы: ${JSON.stringify(task.files)}

СПЕКА:
${task.spec}

ОТЧЁТ ИСПОЛНИТЕЛЯ:
${JSON.stringify(written)}

Проверяй строго: имена функций/полей/роутов совпадают со спекой, обработка ошибок на месте,
заглушек и отладочного вывода нет, код совместим с Python 3.12 и не ломает соседние вызовы.
В issues пиши конкретно: файл, что не так, что должно быть. Пустой массив — только если всё чисто.`;

phase("worker-coding");

const results = await pipeline(
  tasks,
  (prev, task) =>
    agent(writerPrompt(task), {
      provider: WRITER_PROVIDER,
      model: task.model || WRITER_MODEL,
      phase: "worker-coding",
      label: `write ${task.id}`,
      schema: writeSchema,
    }),
  (prev, task) =>
    agent(reviewPrompt(task, prev), {
      provider: REVIEW_PROVIDER,
      model: REVIEW_MODEL,
      phase: "orchestrator-review",
      label: `review ${task.id}`,
      schema: reviewSchema,
    }).then((rev) => ({ id: task.id, ...(prev || {}), review: rev })),
);

const done = results.filter(Boolean);
return {
  total: tasks.length,
  done: done.length,
  failed: done.filter((r) => !r.ok || !r.review || !r.review.ok),
};

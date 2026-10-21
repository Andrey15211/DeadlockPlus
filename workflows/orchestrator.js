// Workflow: Orchestrator (Opus/GPT-6) -> Fan-out Coders (DeepSeek/GLM Flash) -> Test -> Review

export default async function runWorkflow({ args, agent, exec }) {
  const { tasks } = args;
  // tasks = [{ id: "auth", targetFiles: ["src/auth.ts"], spec: "Реализуй JWT валидатор..." }]

  // 1. ФАЗА: Параллельная генерация кода дешевыми китайскими моделями
  const codingResults = await Promise.all(
    tasks.map((task) =>
      agent(
        `Ты — исполнитель кода. Реализуй задачу строго по спецификации.
Спецификация:
${task.spec}

Файлы для изменения: ${JSON.stringify(task.targetFiles)}
Требования:
- Пиши только чистый код без заглушек (TODO) и пропусков.
- Не выходи за рамки указанных файлов.`,
        {
          provider: "zexkora-openai",
          model: "gemini-3.8-low", // или "GLM-5.3-flash"
          phase: "worker-coding",
          schema: {
            type: "object",
            properties: {
              ok: { type: "boolean" },
              changedFiles: { type: "array", items: { type: "string" } },
              summary: { type: "string" },
            },
            required: ["ok", "changedFiles", "summary"],
          },
        },
      ),
    ),
  );

  // 2. ФАЗА: Автоматический прогон сборки / тестов на уровне среды
  // Проверяем, не сломали ли дешевые модели синтаксис
  const check = await exec("npm run build && npm test");

  // 3. ФАЗА: Итоговое ревью и мерж флагманом (Opus 5.5 или GPT-6 Sol)
  const review = await agent(
    `Проанализируй изменения, сделанные воркерами.
Результаты кодеров: ${JSON.stringify(codingResults)}
Результат автотестов: ${check.exitCode === 0 ? "PASSED" : "FAILED"}
Вывод терминала:
${check.stdout || check.stderr}

Изучи итоговый diff файлов. Если тесты упали — укажи точную причину. Если все чисто — дай подтверждение на фиксацию изменений.`,
    {
      provider: "zexkora-anthropic", // или "zexkora-openai"
      model: "claude-opus-5.5", // или "gpt-6-sol"
      phase: "orchestrator-review",
    },
  );

  return { codingResults, testStatus: check.exitCode, review };
}

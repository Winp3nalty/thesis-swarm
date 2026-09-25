import OpenAI from 'openai';

// Point this to your local OpenAI-compatible endpoint from Week 1
const openai = new OpenAI({
    baseURL: 'http://127.0.0.1:11434/v1', // Update port if needed
    apiKey: 'local-key' // Not strictly required for local, but good practice
});

const MODEL_NAME = 'qwen2.5'; // Ensure this matches your local model tag

async function runEdictHandover() {
    console.log("==========================================");
    console.log("[SYSTEM] TASK INITIATED: Draft server maintenance checklist");
    console.log("==========================================\n");

    try {
        // 1. Planner (中书省 - Secretariat) generates the plan
        console.log("[PLANNER] 中书省: Generating initial task breakdown...");
        const plannerResponse = await openai.chat.completions.create({
            model: MODEL_NAME,
            messages: [
                { role: "system", content: "You are the Task Planner. Briefly list 3 steps for server maintenance." }
            ],
            temperature: 0.3,
        });
        
        const planContent = plannerResponse.choices[0].message.content;
        console.log("[PLANNER] 中书省: Task breakdown generated successfully.\n");
        
        // 2. The Handover
        console.log("[SYSTEM] HANDOVER: Routing payload from Planner to Auditor...\n");
        
        // 3. Auditor (门下省 - Chancellery) reviews the plan
        console.log("[AUDITOR] 门下省: Reviewing submitted plan...");
        const auditorResponse = await openai.chat.completions.create({
            model: MODEL_NAME,
            messages: [
                { role: "system", content: "You are the Task Auditor. Reply only with 'Status: APPROVED - The steps are logical.' or 'Status: REJECTED'." },
                { role: "user", content: `Review this plan:\n${planContent}` }
            ],
            temperature: 0.1,
        });

        const auditResult = auditorResponse.choices[0].message.content;
        
        // Final Clean CLI Log
        console.log(`[AUDITOR] 门下省: Review complete. ${auditResult}`);
        console.log("\n==========================================");
        console.log("[SYSTEM] WORKFLOW COMPLETE");
        console.log("==========================================");

    } catch (error) {
        console.error("\n[ERROR] Connection failed. Ensure your local API is running.", error.message);
    }
}

runEdictHandover();
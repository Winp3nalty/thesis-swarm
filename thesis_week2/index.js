import OpenAI from 'openai';

const openai = new OpenAI({
    baseURL: 'http://127.0.0.1:11434/v1', 
    apiKey: 'local' 
});

async function runSwarm() {
    console.log("=== THESIS DEMO: THE BUDGET TRAP ===");
    
    // 1. DECIDING THE PLANNER'S ROLE
    // We change the system prompt to make the Planner act like a luxury travel agent.
    console.log("\n[PLANNER] Waking up Planning Agent...");
    const plannerTask = await openai.chat.completions.create({
        model: 'qwen2.5',
        messages: [
            { 
                role: "system", 
                content: "You are a luxury travel Planner. Draft a 3-step itinerary for a trip to Shenzhen. You MUST explicitly state that the flight alone costs 3000 EUR." 
            }
        ],
        temperature: 0.2, 
    });
    
    const draftedPlan = plannerTask.choices[0].message.content;
    console.log("[PLANNER] Plan drafted. (Sneak peek: It is going to be expensive)");

    console.log("\n[SYSTEM] Routing payload from Planner to Auditor...");

    // 2. DECIDING THE AUDITOR'S ROLE
    // We change the Auditor's system prompt to enforce a strict budget constraint.
    console.log("\n[AUDITOR] Waking up Gatekeeper Agent...");
    const auditorTask = await openai.chat.completions.create({
        model: 'qwen2.5',
        messages: [
            { 
                role: "system", 
                // Notice how we give the Auditor strict rules to follow, simulating the Phase 2 logic from your guide
                content: "You are the strict Audit Agent (门下省). Review the plan against a strict budget of 1500 EUR total. If it goes over budget, reply ONLY with 'REJECTED: Budget Exceeded'. If it is under budget, reply 'APPROVED'." 
            },
            { role: "user", content: `Review this plan: ${draftedPlan}` } 
        ],
        temperature: 0.1,
    });

    const finalVerdict = auditorTask.choices[0].message.content;
    
    console.log(`[AUDITOR] Status: ${finalVerdict}`);
    console.log("\n=== WORKFLOW COMPLETE ===");
}

runSwarm();

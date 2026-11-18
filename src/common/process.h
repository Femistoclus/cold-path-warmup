#pragma once

namespace coldpath {

void PinToCpu(int cpu);
void MakeRealtime(int priority = 50);
void SetTimerSlack(unsigned long ns = 1);
void LockMemory();

} // namespace coldpath

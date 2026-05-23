#include "include/result.hh"
#include "include/common.hh"

#include "../../include/cache_line_size.hh"
#include "../../include/result.hh"

using namespace std;

alignas(CACHE_LINE_SIZE) std::vector<Result> RCSSNResult;

void initResult() { RCSSNResult.resize(TotalThreadNum); }

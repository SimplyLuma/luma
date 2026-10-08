// SPDX-License-Identifier: Apache-2.0
// Project Luma host wrapper around AOSP LatinIME's native decoder core.

#include <algorithm>
#include <array>
#include <cctype>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <vector>

#include "defines.h"
#include "dictionary/property/ngram_context.h"
#include "dictionary/structure/dictionary_structure_with_buffer_policy_factory.h"
#include "suggest/core/dictionary/dictionary.h"
#include "suggest/core/layout/proximity_info.h"
#include "suggest/core/result/suggestion_results.h"
#include "suggest/core/session/dic_traverse_session.h"
#include "suggest/core/suggest_options.h"

namespace {
using namespace latinime;

struct Geometry {
    static constexpr int width = 1000;
    static constexpr int height = 400;
    static constexpr int gridWidth = 10;
    static constexpr int gridHeight = 4;
    std::vector<int> code;
    std::vector<int> x;
    std::vector<int> y;
    std::vector<int> w;
    std::vector<int> h;
    std::vector<float> centerX;
    std::vector<float> centerY;
    std::vector<float> radius;
    std::vector<int> proximity;

    Geometry() {
        addRow("qwertyuiop", 0, 0);
        addRow("asdfghjkl", 50, 100);
        addRow("zxcvbnm", 150, 200);
        addKey(' ', 200, 300, 600, 100);

        proximity.assign(gridWidth * gridHeight * MAX_PROXIMITY_CHARS_SIZE,
            NOT_A_CODE_POINT);
        for (int gy = 0; gy < gridHeight; ++gy) {
            for (int gx = 0; gx < gridWidth; ++gx) {
                const float cx = gx * 100.0f + 50.0f;
                const float cy = gy * 100.0f + 50.0f;
                std::vector<std::pair<float, int>> nearby;
                for (size_t i = 0; i < code.size(); ++i) {
                    const float dx = centerX[i] - cx;
                    const float dy = centerY[i] - cy;
                    nearby.emplace_back(dx * dx + dy * dy, code[i]);
                }
                std::sort(nearby.begin(), nearby.end());
                const int base = (gy * gridWidth + gx) * MAX_PROXIMITY_CHARS_SIZE;
                for (size_t i = 0; i < nearby.size() && i < MAX_PROXIMITY_CHARS_SIZE; ++i)
                    proximity[base + i] = nearby[i].second;
            }
        }
    }

    void addRow(const char *keys, int offset, int top) {
        for (int i = 0; keys[i]; ++i)
            addKey(keys[i], offset + i * 100, top, 100, 100);
    }

    void addKey(int key, int left, int top, int keyWidth, int keyHeight) {
        code.push_back(key);
        x.push_back(left);
        y.push_back(top);
        w.push_back(keyWidth);
        h.push_back(keyHeight);
        centerX.push_back(left + keyWidth / 2.0f);
        centerY.push_back(top + keyHeight / 2.0f);
        radius.push_back(46.0f);
    }

    int indexFor(int cp) const {
        cp = std::tolower(static_cast<unsigned char>(cp));
        const auto it = std::find(code.begin(), code.end(), cp);
        return it == code.end() ? -1 : static_cast<int>(it - code.begin());
    }
};

std::vector<int> codePoints(const std::string &word) {
    std::vector<int> result;
    for (unsigned char c : word)
        result.push_back(c);
    return result;
}

std::string utf8(const SuggestedWord &word) {
    char output[MAX_WORD_LENGTH * 4 + 1] = {};
    intArrayToCharArray(word.getCodePoint(), word.getCodePointCount(), output, sizeof(output));
    return output;
}

class Decoder {
 public:
    explicit Decoder(const char *dictionaryPath) : geometry_(), dictionary_(nullptr),
            proximity_(Geometry::width, Geometry::height, Geometry::gridWidth,
                Geometry::gridHeight, 100, 100, geometry_.proximity.data(),
                geometry_.code.size(), geometry_.x.data(), geometry_.y.data(),
                geometry_.w.data(), geometry_.h.data(), geometry_.code.data(),
                geometry_.centerX.data(), geometry_.centerY.data(), geometry_.radius.data()) {
        struct stat info {};
        if (stat(dictionaryPath, &info) != 0)
            return;
        auto policy = DictionaryStructureWithBufferPolicyFactory::newPolicyForExistingDictFile(
            dictionaryPath, 0, static_cast<int>(info.st_size), false);
        if (policy)
            dictionary_ = std::make_unique<Dictionary>(nullptr, std::move(policy));
    }

    bool ready() const { return dictionary_ != nullptr; }

    std::vector<SuggestedWord> suggest(const std::string &typed, const std::string &previous,
            const std::vector<std::pair<int, int>> &touches) {
        std::vector<int> input = codePoints(typed);
        if (input.empty())
            return predict(previous);

        std::vector<int> xs(input.size());
        std::vector<int> ys(input.size());
        std::vector<int> times(input.size());
        std::vector<int> pointerIds(input.size(), 0);
        for (size_t i = 0; i < input.size(); ++i) {
            const int index = geometry_.indexFor(input[i]);
            xs[i] = index >= 0 ? static_cast<int>(geometry_.centerX[index]) : NOT_A_COORDINATE;
            ys[i] = index >= 0 ? static_cast<int>(geometry_.centerY[index]) : NOT_A_COORDINATE;
            if (i < touches.size() && touches[i].first >= 0 && touches[i].second >= 0) {
                xs[i] = std::clamp(touches[i].first, 0, Geometry::width - 1);
                ys[i] = std::clamp(touches[i].second, 0, Geometry::height - 1);
            }
            times[i] = static_cast<int>(i * 80);
        }

        const auto previousCodePoints = codePoints(previous);
        const NgramContext context = previousCodePoints.empty()
            ? NgramContext()
            : NgramContext(previousCodePoints.data(), previousCodePoints.size(), false);
        const int optionValues[] = {0, 1, 1, 0, 1000};
        const SuggestOptions options(optionValues, 5);
        DicTraverseSession session(nullptr, nullptr, false);
        SuggestionResults results(8);
        dictionary_->getSuggestions(&proximity_, &session, xs.data(), ys.data(), times.data(),
            pointerIds.data(), input.data(), input.size(), &context, &options,
            NOT_A_WEIGHT_OF_LANG_MODEL_VS_SPATIAL_MODEL, &results);
        return results.getSortedSuggestions();
    }

 private:
    std::vector<SuggestedWord> predict(const std::string &previous) {
        const auto previousCodePoints = codePoints(previous);
        if (previousCodePoints.empty())
            return {};
        const NgramContext context(previousCodePoints.data(), previousCodePoints.size(), false);
        SuggestionResults results(8);
        dictionary_->getPredictions(&context, &results);
        return results.getSortedSuggestions();
    }

    Geometry geometry_;
    std::unique_ptr<Dictionary> dictionary_;
    ProximityInfo proximity_;
};

std::vector<std::pair<int, int>> parseTouches(const std::string &encoded) {
    std::vector<std::pair<int, int>> result;
    std::istringstream stream(encoded);
    std::string item;
    while (std::getline(stream, item, ';')) {
        const auto comma = item.find(',');
        if (comma == std::string::npos)
            continue;
        try {
            result.emplace_back(std::stoi(item.substr(0, comma)),
                std::stoi(item.substr(comma + 1)));
        } catch (...) {
            return {};
        }
    }
    return result;
}
} // namespace

int main(int argc, char **argv) {
    if (argc != 2) {
        std::cerr << "usage: luma-latinime-decoder DICTIONARY\n";
        return 2;
    }
    Decoder decoder(argv[1]);
    if (!decoder.ready()) {
        std::cerr << "dictionary-open-failed\n";
        return 3;
    }
    std::cout << "READY\n" << std::flush;
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream fields(line);
        std::string typed;
        std::string previous;
        std::string encodedTouches;
        std::getline(fields, typed, '\t');
        std::getline(fields, previous, '\t');
        std::getline(fields, encodedTouches, '\t');
        const auto words = decoder.suggest(typed, previous, parseTouches(encodedTouches));
        std::cout << "R";
        for (const auto &word : words) {
            std::cout << '\t' << utf8(word) << ':' << word.getType() << ':' << word.getScore();
        }
        std::cout << '\n' << std::flush;
    }
    return 0;
}

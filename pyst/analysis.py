from .mop import *
from .syntax import *
from .asg import *
from .environment import *

class ASGBetaSubstitutionContext:
    def __init__(self) -> None:
        self.substitutionTable = {}

    def setSubstitutionForNode(self, oldNode: ASGNode, replacedNode: ASGNode):
        self.substitutionTable[oldNode] = replacedNode

    def getSubstitutionFor(self, node):
        return self.substitutionTable.get(node, node)

    def isEmpty(self) -> bool:
        return len(self.substitutionTable) == 0
    
    def includesNode(self, node) -> bool:
        return node in self.substitutionTable
    
    def includesAnyOf(self, listOfNodes) -> bool:
        for node in listOfNodes:
            if self.includesNode(node):
                return True
        return False

class ASGReductionAlgorithm(ASGDynamicProgrammingReductionAlgorithm):
    @asgPatternMatchingOnNodeKind(ASGApplicationNode, when = lambda n: n.isLiteralAlwaysReducedPrimitiveApplication() or n.isLiteralPureCompileTimePrimitiveApplication())
    def reduceLiteralApplicationNode(self, node: ASGApplicationNode) -> ASGNode:
        return node.functional.reduceApplicationWithAlgorithm(node, self)

class ASGBetaSubstitutionAlgorithm(ASGDynamicProgrammingAlgorithm):
    def __init__(self, substitutionContext: ASGBetaSubstitutionContext, builder: ASGBuilderWithGVN) -> None:
        super().__init__()
        self.substitutionContext = substitutionContext
        self.builder = builder

    def expandNode(self, node: ASGNode):
        if self.substitutionContext.isEmpty():
            return node
        
        if self.substitutionContext.includesNode(node):
            return self.substitutionContext.getSubstitutionFor(node)
        
        betaReplaceableDependencies = node.betaReplaceableDependencies()
        if not self.substitutionContext.includesAnyOf(betaReplaceableDependencies):
            return node

        return self(node)

    @asgPatternMatchingOnNodeKind(ASGBetaReplaceableNode)
    def expandBetaReplaceableNode(self, node: ASGBetaReplaceableNode) -> ASGAnalyzedNode:
        if not self.substitutionContext.includesNode(node):
            return self.expandGenericNodeRecursively(node)
        else:
            return self.substitutionContext.getSubstitutionFor(node)
        
    def expandParameter(self, parameter):
        if isinstance(parameter, ASGNode):
            return self.expandNode(parameter)
        elif isinstance(parameter, tuple):
            return tuple(map(self.expandParameter, parameter))
        else:
            return parameter

    @asgPatternMatchingOnNodeKind(ASGNode)
    def expandGenericNode(self, node: ASGNode) -> ASGAnalyzedNode:
        return self.expandGenericNodeRecursively(node)
    
    def expandGenericNodeRecursively(self, node: ASGNode):
        nodeAttributes = node.getAllConstructionAttributes()
        expandedParameters = []
        for attribute in nodeAttributes:
            expandedParameters.append(self.expandParameter(attribute))
        return node.__class__(*expandedParameters)

class ASGAnalysisErrorAcumulator:
    def __init__(self) -> None:
        self.errorList = []

    def addError(self, error: ASGErrorNode):
        self.errorList.append(error)

class ASGExpansionAndAnalysisAlgorithm(ASGDynamicProgrammingAlgorithm):
    def __init__(self, environment: ASGEnvironment, builder: ASGBuilderWithGVNAndEnvironment = None, reductionAlgorithm: ASGReductionAlgorithm = None, errorAccumulator = None) -> None:
        super().__init__()
        self.environment = environment
        self.builder = builder
        self.reductionAlgorithm = reductionAlgorithm
        self.errorAccumulator = errorAccumulator
        if self.builder is None:
            self.builder = ASGBuilderWithGVNAndEnvironment(None, self.environment.getTopLevelTargetEnvironment())
        if self.reductionAlgorithm is None:
            self.reductionAlgorithm = ASGReductionAlgorithm()
        if self.errorAccumulator is None:
            self.errorAccumulator = ASGAnalysisErrorAcumulator()

    def withDivergingEnvironment(self, newEnvironment: ASGEnvironment):
        return ASGExpansionAndAnalysisAlgorithm(newEnvironment, ASGBuilderWithGVNAndEnvironment(self.builder, newEnvironment.getTopLevelTargetEnvironment()), self.reductionAlgorithm, self.errorAccumulator)

    def withFunctionalAnalysisEnvironment(self, newEnvironment: ASGFunctionalAnalysisEnvironment):
        return self.withDivergingEnvironment(newEnvironment)

    def postProcessResult(self, result):
        return self.reductionAlgorithm(result.asASGNode())
    
    def withChildLexicalEnvironmentDo(self, newEnvironment: ASGEnvironment, aBlock):
        oldEnvironment = self.environment
        self.environment = newEnvironment
        try:
            return aBlock()
        finally:
            self.environment = oldEnvironment
        
    def attemptExpansionOfNode(self, node: ASGNode) -> tuple[ASGNode, list[ASGNode]]:
        builderMemento = self.builder.memento()
        errorMemento = self.errorAccumulator

        self.errorAccumulator = ASGAnalysisErrorAcumulator()
        expansionResult = self(node)
        expansionErrors = self.errorAccumulator.errorList
        self.errorAccumulator = errorMemento

        if len(expansionErrors) != 0:
            self.builder.restoreMemento(builderMemento)

        return expansionResult, expansionErrors
        
    def makeErrorAtNode(self, message: str, node: ASGNode) -> ASGAnalyzedNode:
        type = self.builder.topLevelIdentifier('Abort')
        innerNodes = []
        if not node.isSyntaxNode():
            innerNodes = [node]
        errorNode = self.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGErrorNode, type, message, innerNodes)
        self.errorAccumulator.addError(errorNode.asASGDataNode())
        return errorNode
    
    def evaluateSymbol(self, node: ASGNode) -> str:
        analyzedNode = self(node).asASGDataNode()
        if analyzedNode.isLiteralSymbolNode():
            return analyzedNode.value
        else:
            return None

    def evaluateOptionalSymbol(self, node: ASGNode) -> str:
        if node is None:
            return None
        
        return self.evaluateSymbol(node)

    def expandMacrosOnly(self, node: ASGNode) -> ASGNode:
        # TODO: Implement this properly.
        return node

    @asgPatternMatchingOnNodeKind(ASGSyntaxErrorNode)
    def expandSyntaxErrorNode(self, node: ASGSyntaxErrorNode) -> ASGAnalyzedNode:
        return self.makeErrorAtNode(node.message, node)

    @asgPatternMatchingOnNodeKind(ASGSyntaxBinaryExpressionSequenceNode)
    def expandSyntaxBinaryExpressionSequenceNode(self, node: ASGSyntaxBinaryExpressionSequenceNode) -> ASGAnalyzedNode:
        elementCount = len(node.elements)
        assert elementCount >= 1

        previous = node.elements[0]
        i = 1
        derivation = ASGNodeSyntaxExpansionDerivation(self, node)
        while i < elementCount:
            operator = node.elements[i]
            operand = node.elements[i + 1]
            previous = ASGSyntaxMessageSendNode(derivation, previous, operator, [operand])
            i += 2

        return self.fromNodeContinueExpanding(node, previous)
    
    def expandTopLevelScript(self, node: ASGNode) -> ASGTopLevelScriptNode:
        entryPoint = self.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGSequenceEntryNode)
        scriptResult = self(node)
        exitPoint = self.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGSequenceReturnNode, scriptResult, predecessor = self.builder.currentPredecessor)
        return self.builder.forSyntaxExpansionBuild(self, node, ASGTopLevelScriptNode, entryPoint, exitPoint = exitPoint)

    @asgPatternMatchingOnNodeKind(ASGSyntaxLiteralIntegerNode)
    def expandSyntaxLiteralIntegerNode(self, node: ASGSyntaxLiteralIntegerNode) -> ASGAnalyzedNode:
        return self.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGLiteralIntegerNode, node.value)

    @asgPatternMatchingOnNodeKind(ASGSyntaxLiteralSymbolNode)
    def expandSyntaxLiteralSymbolNode(self, node: ASGSyntaxLiteralSymbolNode) -> ASGAnalyzedNode:
        return self.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGLiteralSymbolNode,  node.value)

    @asgPatternMatchingOnNodeKind(ASGSyntaxLiteralStringNode)
    def expandSyntaxLiteralStringNode(self, node: ASGSyntaxLiteralSymbolNode) -> ASGAnalyzedNode:
        return self.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGLiteralStringNode,  node.value)

    @asgPatternMatchingOnNodeKind(ASGSyntaxIdentifierReferenceNode)
    def expandSyntaxIdentifierReferenceNode(self, node: ASGSyntaxIdentifierReferenceNode) -> ASGAnalyzedNode:
        binding = self.environment.lookSymbolBindingRecursively(node.value)
        if binding is None:
            return self.makeErrorAtNode('Failed to finding binding for symbol %s.' % node.value, node)
        else:
            return self(binding)

    @asgPatternMatchingOnNodeKind(ASGSyntaxMessageSendNode, when = lambda n: n.receiver is None)
    def expandSyntaxMessageSendNodeWithoutReceiver(self, node: ASGSyntaxMessageSendNode) -> ASGAnalyzedNode:
        return self.expandFunctionalApplicationMessageSendNode(node)

    @asgPatternMatchingOnNodeKind(ASGSyntaxMessageSendNode, when = lambda n: n.receiver is not None)
    def expandSyntaxMessageSendNodeWithReceiver(self, node: ASGSyntaxMessageSendNode) -> ASGAnalyzedNode:
        receiver = self(node.receiver)
        receiverType = receiver.getTypeInEnvironment(self.environment)
        return receiverType.expandSyntaxMessageSendNode(self, node)
    
    def attemptToEvaluateMessageSendSelector(self, node: ASGSyntaxMessageSendNode) -> str:
        symbolType = self.builder.topLevelIdentifier('Symbol')
        analyzedSelector, typechecked = self.analyzeNodeWithExpectedType(node.selector, symbolType)
        if not typechecked:
            return None
        
        analyzedSelector = analyzedSelector.asASGDataNode()
        if analyzedSelector.isLiteralNode():
            return analyzedSelector.value
        else:
            return None

    def expandFunctionalApplicationMessageSendNode(self, node: ASGSyntaxMessageSendNode) -> ASGAnalyzedNode:
        selectorValue = self.evaluateSymbol(node.selector)
        if selectorValue is None:
            ## Analyze the the receiver and the arguments to discover more errors.
            if node.receiver is not None: self(node.receiver)
            for arg in node.arguments:
                self(arg)
            return self.makeErrorAtNode('Cannot expand message send node without constant selector.', node)

        selectorIdentifier = ASGSyntaxIdentifierReferenceNode(ASGNodeSyntaxExpansionDerivation(self, node), selectorValue)
        applicationArguments = []
        if node.receiver is not None:
            applicationArguments.append(node.receiver)
        applicationArguments += node.arguments
        
        application = ASGSyntaxApplicationNode(ASGNodeSyntaxExpansionDerivation(self, node), selectorIdentifier, applicationArguments)
        return self.fromNodeContinueExpanding(node, application)

    @asgPatternMatchingOnNodeKind(ASGSyntaxApplicationNode)
    def expandSyntaxApplicationNode(self, node: ASGSyntaxApplicationNode) -> ASGAnalyzedNode:
        self.syntaxPredecessorOf(node)
        functional = self(node.functional)
        functionalType = functional.getTypeInEnvironment(self.environment)
        return functionalType.expandSyntaxApplicationNode(self, node)

    def analyzeDivergentBranchExpression(self, node: ASGNode) -> tuple[ASGSequenceEntryNode, ASGNode]:
        branchAnalyzer = self.withDivergingEnvironment(ASGLexicalEnvironment(self.environment, node.sourceDerivation.getSourcePosition()))
        entryPoint = branchAnalyzer.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGSequenceEntryNode)
        branchResult = branchAnalyzer(node)
        exitPoint = branchAnalyzer.builder.currentPredecessor
        return entryPoint, exitPoint, branchResult, branchAnalyzer

    def analyzeOptionalDivergentBranchExpression(self, node: ASGNode) -> tuple[ASGSequenceEntryNode, ASGNode]:
        if node is not None:
            return self.analyzeDivergentBranchExpression(node)
        
        assert False
        
    def mergeTypesOfBranches(self, branches: list[ASGNode]):
        if len(branches) == 0:
            return self.builder.topLevelIdentifier('Void')
        
        mergedBranchType = None
        for branch in branches:
            branchType = branch.getTypeInEnvironment(self.environment)
            if mergedBranchType is None:
                mergedBranchType = branchType
            elif not branchType.unificationEquals(mergedBranchType):
                return None
        return mergedBranchType

    @asgPatternMatchingOnNodeKind(ASGSyntaxSequenceNode)
    def expandSyntaxSequenceNode(self, node: ASGSyntaxSequenceNode) -> ASGAnalyzedNode:
        self.syntaxPredecessorOf(node)
        if len(node.elements) == 0:
            return self.builder.forSyntaxExpansionBuildAndSequence(self, node, ASGLiteralNilNode)

        elementsToExpand = node.elements
        for i in range(len(elementsToExpand)):
            if i + 1 < len(elementsToExpand):
                self(elementsToExpand[i])
            else:
                return self.fromNodeContinueExpanding(node, elementsToExpand[i])
        assert False, "Should not reach here."

    @asgPatternMatchingOnNodeKind(ASGAnalyzedNode)
    def expandSyntaxTypecheckedNode(self, node: ASGAnalyzedNode) -> ASGAnalyzedNode:
        return node

def expandAndAnalyze(environment: ASGEnvironment, node: ASGNode):
    expander = ASGExpansionAndAnalysisAlgorithm(environment)
    result = expander.expandTopLevelScript(node)
    return result, expander.errorAccumulator.errorList